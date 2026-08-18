from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from atlanticus.data_producers.fabrica.models import (
    FabricaKpiStreamDefinition,
    FabricaPlanStreamDefinition,
    FabricaSourceBlob,
    FabricaStreamDefinition,
)
from atlanticus.data_producers.fabrica.source import FabricaStorageSource
from atlanticus.data_producers.fabrica.transform import (
    build_partition_frames,
    merge_partition_frame,
)
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
class FabricaPartitionPublication:
    partition_key: str
    publication: DatasetPublicationResult


@dataclass(frozen=True, slots=True)
class FabricaMaterializationResult:
    source_blob: FabricaSourceBlob
    source_row_count: int
    publications: tuple[FabricaPartitionPublication, ...]
    unknown_source_values: tuple[str, ...]
    metrics_expected: int
    metrics_present: int
    missing_metric_keys: tuple[str, ...]
    missing_metric_keys_by_output: tuple[tuple[str, tuple[str, ...]], ...]

    @property
    def partitions_changed(self) -> int:
        return sum(
            item.publication.status is PublicationStatus.COMMITTED for item in self.publications
        )

    @property
    def new_data(self) -> bool:
        return self.partitions_changed > 0

    @property
    def publication_signatures(self) -> dict[str, str]:
        return {
            item.partition_key: item.publication.content_signature
            for item in self.publications
            if item.publication.content_signature
        }


class FabricaMaterializer:
    def __init__(
        self,
        *,
        source: FabricaStorageSource,
        runtime: DatasetRuntime,
        definition: FabricaStreamDefinition,
        dataset_namespace: tuple[str, ...] = ('fabrica',),
    ) -> None:
        if not isinstance(source, FabricaStorageSource):
            raise TypeError('source must be a FabricaStorageSource')
        if not isinstance(runtime, DatasetRuntime):
            raise TypeError('runtime must be a DatasetRuntime')
        self._source = source
        self._runtime = runtime
        self.definition = definition
        self._dataset = _build_dataset_definition(definition, namespace=dataset_namespace)

    @property
    def catalog_signature(self) -> str:
        payload = _catalog_signature_payload(self.definition)
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return f'sha256:{hashlib.sha256(encoded).hexdigest()}'

    def latest_source(self, *, prefix: str) -> FabricaSourceBlob | None:
        return self._source.latest(prefix=prefix)

    def materialize(self, *, source_blob: FabricaSourceBlob) -> FabricaMaterializationResult:
        path = self._source.download(blob_name=source_blob.name)
        try:
            source_table = self._source.read_selected_columns(
                path=path,
                metric_ids=tuple(_metric_source_id(metric) for metric in self.definition.metrics),
            )
            transformed = build_partition_frames(table=source_table, definition=self.definition)
            publications: list[FabricaPartitionPublication] = []
            for frame_key, materialization_name, metrics in _materialization_specs(self.definition):
                if not metrics:
                    continue
                target = self._dataset.resolve_target(materialization=materialization_name)
                current = self._read_current(target=target)
                merged = merge_partition_frame(
                    current=current,
                    incoming=transformed.frames[frame_key],
                    metrics=metrics,
                )
                publication = self._runtime.replace(
                    definition=self._dataset,
                    target=target,
                    data=merged,
                )
                publications.append(
                    FabricaPartitionPublication(
                        partition_key=materialization_name,
                        publication=publication,
                    )
                )
            return FabricaMaterializationResult(
                source_blob=source_blob,
                source_row_count=transformed.source_row_count,
                publications=tuple(publications),
                unknown_source_values=transformed.unknown_source_values,
                metrics_expected=transformed.metrics_expected,
                metrics_present=transformed.metrics_present,
                missing_metric_keys=transformed.missing_metric_keys,
                missing_metric_keys_by_output=transformed.missing_metric_keys_by_output,
            )
        finally:
            Path(path).unlink(missing_ok=True)

    def _read_current(self, *, target: DatasetTarget):
        try:
            result = self._runtime.read_dataframe(definition=self._dataset, target=target)
        except DatasetRuntimeNotFoundError:
            return None
        return result.dataframe


def _catalog_signature_payload(definition: FabricaStreamDefinition) -> dict[str, object]:
    base: dict[str, object] = {
        'stream_key': definition.stream_key,
        'output_route_segment': definition.output_route_segment,
    }
    if isinstance(definition, FabricaPlanStreamDefinition):
        base['partitions'] = [
            {
                'key': partition.key.value,
                'source_value': partition.source_value,
                'route_segment': partition.route_segment,
            }
            for partition in definition.partitions
        ]
        base['metrics'] = [
            {
                'id_kpi': metric.id_kpi,
                'metric_key': metric.metric_key,
                'value_kind': metric.value_kind.value,
                'partitions': [partition.value for partition in metric.partitions],
            }
            for metric in definition.metrics
        ]
        return base
    if isinstance(definition, FabricaKpiStreamDefinition):
        base['datasets'] = [
            {
                'name': dataset.name,
                'level': dataset.level.value,
                'route_segment': dataset.route_segment,
                'metrics': [
                    {
                        'id_kpi': _metric_source_id(metric),
                        'metric_key': metric.metric_key,
                        'value_kind': metric.value_kind.value,
                    }
                    for metric in dataset.metrics
                ],
            }
            for dataset in definition.datasets
        ]
        return base
    raise TypeError(f'Unsupported Fabrica stream definition: {type(definition)!r}')


def _materialization_specs(
    definition: FabricaStreamDefinition,
) -> tuple[tuple[object, str, tuple[object, ...]], ...]:
    if isinstance(definition, FabricaPlanStreamDefinition):
        return tuple(
            (
                partition.key,
                partition.key.value,
                tuple(
                    metric for metric in definition.metrics if partition.key in metric.partitions
                ),
            )
            for partition in definition.partitions
        )
    if isinstance(definition, FabricaKpiStreamDefinition):
        return tuple(
            (dataset.name, dataset.name, tuple(dataset.metrics)) for dataset in definition.datasets
        )
    raise TypeError(f'Unsupported Fabrica stream definition: {type(definition)!r}')


def _metric_source_id(metric: object) -> str:
    value = metric.id_kpi
    return str(getattr(value, 'value', value)).strip().upper()


def _build_dataset_definition(
    definition: FabricaStreamDefinition,
    *,
    namespace: tuple[str, ...],
) -> DatasetDefinition:
    normalized_namespace = tuple(str(item).strip() for item in namespace if str(item).strip())
    if not normalized_namespace:
        raise ValueError('dataset_namespace must not be empty')
    if isinstance(definition, FabricaPlanStreamDefinition):
        materializations = tuple(
            MaterializationDefinition(
                name=partition.key.value,
                layout=SingleArtifactLayout(),
                route_segments=(partition.route_segment,),
            )
            for partition in definition.partitions
        )
    elif isinstance(definition, FabricaKpiStreamDefinition):
        materializations = tuple(
            MaterializationDefinition(
                name=dataset.name,
                layout=SingleArtifactLayout(),
                route_segments=(dataset.route_segment,),
            )
            for dataset in definition.datasets
        )
    else:
        raise TypeError(f'Unsupported Fabrica stream definition: {type(definition)!r}')
    return DatasetDefinition(
        key=DatasetKey(namespace=normalized_namespace, name=definition.stream_key),
        route_segments=(*normalized_namespace, definition.output_route_segment),
        materializations=materializations,
    )
