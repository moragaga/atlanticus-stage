# Se conservan dos estrategias de materialización.
# RemanentesMaterializer mantiene el histórico diario existente para compatibilidad.
# RemanentesLatestMaterializer publica solo el snapshot vigente, reemplazando completamente el anterior.
# La variante latest selecciona el blob pendiente más reciente y puede reconstruir latest desde el watermark si aún no existe.

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from atlanticus.data_producers.remanentes.models import (
    RemanentesSourceBlob,
    RemanentesStocksStreamDefinition,
    RemanentesStreamDefinition,
)
from atlanticus.data_producers.remanentes.source import RemanentesStorageSource
from atlanticus.data_producers.remanentes.transform import merge_snapshot, transform_snapshot
from atlanticus.datasets.layouts import SingleArtifactLayout
from atlanticus.datasets.models import (
    DatasetDefinition,
    DatasetKey,
    DatasetTarget,
    MaterializationDefinition,
)
from atlanticus.datasets.results import DatasetPublicationResult, PublicationStatus
from atlanticus.datasets.runtime import DatasetRuntime, DatasetRuntimeNotFoundError


@dataclass(frozen=True, slots=True)
class RemanentesMaterializationResult:
    source_blob: RemanentesSourceBlob
    source_row_count: int
    output_row_count: int
    publication: DatasetPublicationResult
    present_metric_keys: tuple[str, ...] = ()
    missing_metric_keys: tuple[str, ...] = ()
    unknown_source_values: tuple[str, ...] = ()

    @property
    def new_data(self) -> bool:
        return self.publication.status is PublicationStatus.COMMITTED

    @property
    def partitions_changed(self) -> int:
        return int(self.new_data)

    @property
    def publication_signatures(self) -> dict[str, str]:
        signature = self.publication.content_signature
        if not signature:
            return {}
        return {self.publication.target.identifier: signature}


class RemanentesMaterializer:
    def __init__(
        self,
        *,
        source: RemanentesStorageSource,
        runtime: DatasetRuntime,
        definition: RemanentesStreamDefinition,
        dataset_namespace: tuple[str, ...] = ('remanentes',),
    ) -> None:
        if not isinstance(source, RemanentesStorageSource):
            raise TypeError('source must be a RemanentesStorageSource')
        if not isinstance(runtime, DatasetRuntime):
            raise TypeError('runtime must be a DatasetRuntime')
        self._source = source
        self._runtime = runtime
        self.definition = definition
        self._dataset = _build_dataset_definition(definition, namespace=dataset_namespace)

    @property
    def catalog_signature(self) -> str:
        payload: dict[str, object] = {
            'stream_key': self.definition.stream_key,
            'source_prefix': self.definition.source_prefix,
            'source_timezone_name': self.definition.source_timezone_name,
            'source_filename_pattern': self.definition.source_filename_pattern.pattern,
            'stream_type': type(self.definition).__name__,
        }
        if isinstance(self.definition, RemanentesStocksStreamDefinition):
            payload['stock_metrics'] = [
                {'source_value': metric.source_value, 'metric_key': metric.metric_key}
                for metric in self.definition.stock_metrics
            ]
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return f'sha256:{hashlib.sha256(encoded).hexdigest()}'

    def pending_sources(
        self,
        *,
        now_utc: datetime,
        cursor_timestamp_utc: datetime | None,
        cursor_blob_name: str | None,
        cursor_blob_etag: str | None,
        cursor_blob_last_modified_utc: datetime | None,
    ) -> tuple[RemanentesSourceBlob, ...]:
        return self._source.pending(
            now_utc=now_utc,
            cursor_timestamp_utc=cursor_timestamp_utc,
            cursor_blob_name=cursor_blob_name,
            cursor_blob_etag=cursor_blob_etag,
            cursor_blob_last_modified_utc=cursor_blob_last_modified_utc,
        )

    def materialize(self, *, source_blob: RemanentesSourceBlob) -> RemanentesMaterializationResult:
        source_table = self._source.download_table(blob_name=source_blob.name)
        transformed = transform_snapshot(
            table=source_table,
            definition=self.definition,
            source_timestamp_utc=source_blob.source_file_timestamp_utc,
        )
        timestamp = source_blob.source_file_timestamp_utc
        target = self._dataset.resolve_target(
            materialization='daily',
            partition={
                'year': f'{timestamp:%Y}',
                'month': f'{timestamp:%m}',
                'day': f'{timestamp:%d}',
            },
        )
        current = self._read_current(target=target)
        merged = merge_snapshot(
            current=current,
            incoming=transformed.dataframe,
            source_timestamp_utc=source_blob.source_file_timestamp_utc,
        )
        publication = self._runtime.replace(
            definition=self._dataset,
            target=target,
            data=merged,
        )
        return RemanentesMaterializationResult(
            source_blob=source_blob,
            source_row_count=transformed.source_row_count,
            output_row_count=len(transformed.dataframe),
            publication=publication,
            present_metric_keys=transformed.present_metric_keys,
            missing_metric_keys=transformed.missing_metric_keys,
            unknown_source_values=transformed.unknown_source_values,
        )

    def _read_current(self, *, target: DatasetTarget):
        try:
            result = self._runtime.read_dataframe(definition=self._dataset, target=target)
        except DatasetRuntimeNotFoundError:
            return None
        return result.dataframe


class RemanentesLatestMaterializer(RemanentesMaterializer):
    def __init__(
        self,
        *,
        source: RemanentesStorageSource,
        runtime: DatasetRuntime,
        definition: RemanentesStreamDefinition,
        dataset_namespace: tuple[str, ...] = ('remanentes',),
    ) -> None:
        super().__init__(
            source=source,
            runtime=runtime,
            definition=definition,
            dataset_namespace=dataset_namespace,
        )
        self._dataset = _build_latest_dataset_definition(
            definition,
            namespace=dataset_namespace,
        )

    def pending_sources(
        self,
        *,
        now_utc: datetime,
        cursor_timestamp_utc: datetime | None,
        cursor_blob_name: str | None,
        cursor_blob_etag: str | None,
        cursor_blob_last_modified_utc: datetime | None,
    ) -> tuple[RemanentesSourceBlob, ...]:
        pending = super().pending_sources(
            now_utc=now_utc,
            cursor_timestamp_utc=cursor_timestamp_utc,
            cursor_blob_name=cursor_blob_name,
            cursor_blob_etag=cursor_blob_etag,
            cursor_blob_last_modified_utc=cursor_blob_last_modified_utc,
        )
        if pending:
            return pending[-1:]
        if self._latest_target_exists() or cursor_timestamp_utc is None:
            return ()
        recovery = self._source.pending(
            now_utc=now_utc,
            cursor_timestamp_utc=cursor_timestamp_utc - timedelta(microseconds=1),
            cursor_blob_name=None,
            cursor_blob_etag=None,
            cursor_blob_last_modified_utc=None,
        )
        return recovery[-1:]

    def materialize(self, *, source_blob: RemanentesSourceBlob) -> RemanentesMaterializationResult:
        source_table = self._source.download_table(blob_name=source_blob.name)
        transformed = transform_snapshot(
            table=source_table,
            definition=self.definition,
            source_timestamp_utc=source_blob.source_file_timestamp_utc,
        )
        target = self._dataset.resolve_target(materialization='latest')
        publication = self._runtime.replace(
            definition=self._dataset,
            target=target,
            data=transformed.dataframe,
        )
        return RemanentesMaterializationResult(
            source_blob=source_blob,
            source_row_count=transformed.source_row_count,
            output_row_count=len(transformed.dataframe),
            publication=publication,
            present_metric_keys=transformed.present_metric_keys,
            missing_metric_keys=transformed.missing_metric_keys,
            unknown_source_values=transformed.unknown_source_values,
        )

    def _latest_target_exists(self) -> bool:
        target = self._dataset.resolve_target(materialization='latest')
        try:
            self._runtime.read_schema(definition=self._dataset, target=target)
        except DatasetRuntimeNotFoundError:
            return False
        return True


def _build_dataset_definition(
    definition: RemanentesStreamDefinition,
    *,
    namespace: tuple[str, ...] = ('remanentes',),
) -> DatasetDefinition:
    normalized_namespace = tuple(str(item).strip() for item in namespace if str(item).strip())
    if not normalized_namespace:
        raise ValueError('dataset_namespace must not be empty')
    return DatasetDefinition(
        key=DatasetKey(namespace=normalized_namespace, name=definition.stream_key),
        route_segments=(*normalized_namespace, definition.stream_key),
        materializations=(
            MaterializationDefinition(
                name='daily',
                layout=SingleArtifactLayout(),
                partition_dimensions=('year', 'month', 'day'),
                route_segments=(),
            ),
        ),
    )


def _build_latest_dataset_definition(
    definition: RemanentesStreamDefinition,
    *,
    namespace: tuple[str, ...] = ('remanentes',),
) -> DatasetDefinition:
    normalized_namespace = tuple(str(item).strip() for item in namespace if str(item).strip())
    if not normalized_namespace:
        raise ValueError('dataset_namespace must not be empty')
    return DatasetDefinition(
        key=DatasetKey(namespace=normalized_namespace, name=definition.stream_key),
        route_segments=(*normalized_namespace, definition.stream_key),
        materializations=(
            MaterializationDefinition(
                name='latest',
                layout=SingleArtifactLayout(),
            ),
        ),
    )
