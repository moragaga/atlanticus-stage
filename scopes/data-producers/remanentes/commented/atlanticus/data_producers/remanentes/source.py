# Esta capa conoce Azure Storage y el contrato físico de columnas de Remanentes.
# El catálogo no necesita repetir columnas fijas: stocks y rows tienen schemas fuente conocidos.
# Los blobs pendientes se ordenan por timestamp fuente y nombre para conservar procesamiento determinista.

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

import pyarrow.parquet as pq

from atlanticus.connectivity.storage import StorageBlobProperties, StorageClient
from atlanticus.data_producers.remanentes.errors import RemanentesSourceError
from atlanticus.data_producers.remanentes.models import (
    RemanentesRowsStreamDefinition,
    RemanentesSourceBlob,
    RemanentesStocksStreamDefinition,
    RemanentesStreamDefinition,
    parse_source_timestamp,
)

_STOCK_SOURCE_COLUMNS = ('STOCK', 'Ton (kt)')
_ROW_SOURCE_COLUMNS = (
    'Fase',
    'Banco',
    'Tipo de material',
    'Observación',
    'Ton (kt)',
)


class RemanentesStorageSource:
    def __init__(
        self,
        *,
        client: StorageClient,
        container_name: str,
        definition: RemanentesStreamDefinition,
    ) -> None:
        if not isinstance(client, StorageClient):
            raise TypeError('client must be a StorageClient')
        normalized_container = str(container_name).strip()
        if not normalized_container:
            raise ValueError('container_name is required')
        if not isinstance(
            definition,
            RemanentesStocksStreamDefinition | RemanentesRowsStreamDefinition,
        ):
            raise TypeError('definition must be a Remanentes stream definition')
        self._client = client
        self._container_name = normalized_container
        self.definition = definition

    def pending(
        self,
        *,
        now_utc: datetime,
        cursor_timestamp_utc: datetime | None,
        cursor_blob_name: str | None,
        cursor_blob_etag: str | None,
        cursor_blob_last_modified_utc: datetime | None,
    ) -> tuple[RemanentesSourceBlob, ...]:
        current_day = self.definition.source_local_date(now_utc)
        start_day = (
            current_day
            if cursor_timestamp_utc is None
            else self.definition.source_local_date(cursor_timestamp_utc)
        )
        candidates: list[RemanentesSourceBlob] = []
        for source_day in _date_range(start_day, current_day):
            prefix = self.definition.source_day_prefix(source_day)
            candidates.extend(
                candidate
                for item in self._client.list_blobs(
                    container_name=self._container_name,
                    prefix=prefix,
                )
                for candidate in (self._candidate(item),)
                if candidate is not None
            )
        ordered = tuple(
            sorted(candidates, key=lambda item: (item.source_file_timestamp_utc, item.name))
        )
        if cursor_timestamp_utc is None:
            return ordered
        cursor = (_normalize_utc(cursor_timestamp_utc), cursor_blob_name or '')
        pending: list[RemanentesSourceBlob] = []
        for candidate in ordered:
            candidate_key = (candidate.source_file_timestamp_utc, candidate.name)
            if candidate_key > cursor:
                pending.append(candidate)
                continue
            if candidate_key == cursor and candidate.name == cursor_blob_name:
                if cursor_blob_etag and candidate.etag:
                    if cursor_blob_etag != candidate.etag:
                        pending.append(candidate)
                elif (
                    cursor_blob_last_modified_utc
                    and candidate.last_modified_utc
                    and _normalize_utc(cursor_blob_last_modified_utc) != candidate.last_modified_utc
                ):
                    pending.append(candidate)
        return tuple(pending)

    def download_table(self, *, blob_name: str):
        path: str | None = None
        try:
            with NamedTemporaryFile(
                prefix='atlanticus-remanentes-',
                suffix='.parquet',
                delete=False,
            ) as temporary:
                path = temporary.name
                self._client.download_to(
                    container_name=self._container_name,
                    blob_name=blob_name,
                    target=temporary,
                )
            parquet = pq.ParquetFile(path)
            required = _required_source_columns(self.definition)
            actual_columns = _resolve_columns(
                available=parquet.schema_arrow.names,
                required=required,
            )
            table = pq.read_table(path, columns=actual_columns)
            return table.rename_columns(list(required))
        finally:
            if path is not None:
                Path(path).unlink(missing_ok=True)

    def _candidate(self, item: StorageBlobProperties) -> RemanentesSourceBlob | None:
        timestamp = parse_source_timestamp(definition=self.definition, blob_name=item.name)
        if timestamp is None:
            return None
        return RemanentesSourceBlob(
            name=item.name,
            source_file_timestamp_utc=timestamp,
            size=item.size,
            etag=item.etag,
            last_modified_utc=(
                None if item.last_modified is None else _normalize_utc(item.last_modified)
            ),
        )


def _required_source_columns(definition: RemanentesStreamDefinition) -> tuple[str, ...]:
    if isinstance(definition, RemanentesStocksStreamDefinition):
        return _STOCK_SOURCE_COLUMNS
    if isinstance(definition, RemanentesRowsStreamDefinition):
        return _ROW_SOURCE_COLUMNS
    raise TypeError('definition must be a Remanentes stream definition')


def _resolve_columns(*, available: list[str], required: tuple[str, ...]) -> list[str]:
    by_name = {str(name).strip().casefold(): str(name) for name in available}
    missing = tuple(name for name in required if name.strip().casefold() not in by_name)
    if missing:
        raise RemanentesSourceError(f'Remanentes source is missing required columns: {missing}')
    return [by_name[name.strip().casefold()] for name in required]


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
