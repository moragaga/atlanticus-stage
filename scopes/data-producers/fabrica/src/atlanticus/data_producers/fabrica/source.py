from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

import pyarrow.parquet as pq

from atlanticus.connectivity.storage import StorageBlobProperties, StorageClient
from atlanticus.data_producers.fabrica.errors import FabricaSourceError
from atlanticus.data_producers.fabrica.models import (
    FabricaSourceBlob,
    FabricaStreamDefinition,
    parse_source_file_timestamp,
)

_SOURCE_COLUMNS = (
    'timestamp',
    'id_kpi',
    'valor',
    'nivel',
    'timestamp_ejecucion',
    'particion',
)


class FabricaStorageSource:
    def __init__(
        self,
        *,
        client: StorageClient,
        container_name: str,
        definition: FabricaStreamDefinition,
    ) -> None:
        if not isinstance(client, StorageClient):
            raise TypeError('client must be a StorageClient')
        normalized_container = str(container_name).strip()
        if not normalized_container:
            raise ValueError('container_name is required')
        self._client = client
        self._container_name = normalized_container
        self.definition = definition

    def latest(self, *, prefix: str) -> FabricaSourceBlob | None:
        candidates = tuple(
            value
            for item in self._client.list_blobs(
                container_name=self._container_name,
                prefix=prefix,
            )
            for value in (self._candidate(item),)
            if value is not None
        )
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.source_file_timestamp_utc, item.name))

    def download(self, *, blob_name: str) -> str:
        path: str | None = None
        try:
            with NamedTemporaryFile(
                prefix='atlanticus-fabrica-',
                suffix='.parquet',
                delete=False,
            ) as temporary:
                path = temporary.name
                self._client.download_to(
                    container_name=self._container_name,
                    blob_name=blob_name,
                    target=temporary,
                )
            return path
        except Exception:
            if path is not None:
                Path(path).unlink(missing_ok=True)
            raise

    def read_selected_columns(self, *, path: str, metric_ids: tuple[str, ...]):
        names = self._source_column_names(path=path)
        table = pq.read_table(
            path,
            columns=[names[column] for column in _SOURCE_COLUMNS],
            filters=[(names['id_kpi'], 'in', list(metric_ids))],
        )
        return table.rename_columns(list(_SOURCE_COLUMNS))

    def _source_column_names(self, *, path: str) -> dict[str, str]:
        parquet = pq.ParquetFile(path)
        names = {str(name).strip().lower(): str(name) for name in parquet.schema_arrow.names}
        missing = tuple(column for column in _SOURCE_COLUMNS if column not in names)
        if missing:
            raise FabricaSourceError(f'Fabrica source is missing required columns: {missing}')
        return names

    def _candidate(self, item: StorageBlobProperties) -> FabricaSourceBlob | None:
        timestamp = parse_source_file_timestamp(definition=self.definition, blob_name=item.name)
        if timestamp is None:
            return None
        return FabricaSourceBlob(
            name=item.name,
            source_file_timestamp_utc=timestamp,
            size=item.size,
            etag=item.etag,
            last_modified_utc=(
                None if item.last_modified is None else _normalize_utc(item.last_modified)
            ),
        )


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
