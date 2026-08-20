# Vincula cada source lógico con las particiones físicas que realmente soporta.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from ada.kpis.core import KpiPartition, KpiSource, KpiSourceView
from ada.kpis.sources.errors import KpiSourceBindingError
from atlanticus.datasets.errors import DatasetTargetError
from atlanticus.datasets.models import DatasetDefinition


class TimePartitionGranularity(StrEnum):
    DAY = 'day'
    MONTH = 'month'


@dataclass(frozen=True, slots=True)
class KpiPartitionBinding:
    partition: KpiPartition
    materialization: str
    time_partition_granularity: TimePartitionGranularity | None = None
    timestamp_column: str | None = None
    shift_column: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.partition, KpiPartition):
            raise TypeError('partition must be KpiPartition')
        materialization = _required_text(self.materialization, 'materialization')
        timestamp = _optional_text(self.timestamp_column)
        shift_column = _optional_text(self.shift_column)
        if self.time_partition_granularity is not None and not isinstance(
            self.time_partition_granularity, TimePartitionGranularity
        ):
            raise TypeError('time_partition_granularity must be TimePartitionGranularity or None')
        if self.time_partition_granularity is not None and timestamp is None:
            raise KpiSourceBindingError(
                f'{self.partition.value}: partitioned time binding requires timestamp_column'
            )
        if shift_column is not None and self.time_partition_granularity is not None:
            raise KpiSourceBindingError(
                f'{self.partition.value}: shift binding cannot use time partition granularity'
            )
        object.__setattr__(self, 'materialization', materialization)
        object.__setattr__(self, 'timestamp_column', timestamp)
        object.__setattr__(self, 'shift_column', shift_column)


@dataclass(frozen=True, slots=True)
class KpiSourceBinding:
    source: KpiSource
    definition: DatasetDefinition
    partitions: Mapping[KpiPartition, KpiPartitionBinding]

    def __post_init__(self) -> None:
        if not isinstance(self.source, KpiSource):
            raise TypeError('source must be KpiSource')
        if not isinstance(self.definition, DatasetDefinition):
            raise TypeError(f'{self.source.value}: definition must be DatasetDefinition')
        if not isinstance(self.partitions, Mapping) or not self.partitions:
            raise KpiSourceBindingError(f'{self.source.value}: binding requires partitions')
        normalized: dict[KpiPartition, KpiPartitionBinding] = {}
        for key, partition in self.partitions.items():
            if not isinstance(key, KpiPartition):
                raise TypeError(f'{self.source.value}: partition keys must be KpiPartition values')
            if not isinstance(partition, KpiPartitionBinding):
                raise TypeError(f'{self.source.value}/{key.value}: invalid partition binding')
            if partition.partition is not key:
                raise KpiSourceBindingError(
                    f'{self.source.value}/{key.value}: partition key and binding must match'
                )
            try:
                self.definition.get_materialization(partition.materialization)
            except DatasetTargetError as error:
                raise KpiSourceBindingError(
                    f'{self.source.value}/{key.value}: unknown materialization: '
                    f'{partition.materialization}'
                ) from error
            normalized[key] = partition
        object.__setattr__(self, 'partitions', MappingProxyType(normalized))

    def get_partition(self, partition: KpiPartition) -> KpiPartitionBinding:
        if not isinstance(partition, KpiPartition):
            raise TypeError('partition must be KpiPartition')
        try:
            return self.partitions[partition]
        except KeyError as error:
            raise KpiSourceBindingError(
                f'{self.source.value}: source does not support partition: {partition.value}'
            ) from error


@dataclass(frozen=True, slots=True)
class KpiSourceRegistry:
    bindings: Mapping[KpiSource, KpiSourceBinding]

    def __post_init__(self) -> None:
        if not isinstance(self.bindings, Mapping):
            raise TypeError('bindings must be a mapping')
        normalized: dict[KpiSource, KpiSourceBinding] = {}
        for source, binding in self.bindings.items():
            if not isinstance(source, KpiSource):
                raise TypeError('registry keys must be KpiSource values')
            if not isinstance(binding, KpiSourceBinding):
                raise TypeError(f'{source.value}: binding must be KpiSourceBinding')
            if binding.source is not source:
                raise KpiSourceBindingError(
                    f'{source.value}: registry key and binding source must match'
                )
            normalized[source] = binding
        object.__setattr__(self, 'bindings', MappingProxyType(normalized))

    @property
    def sources(self) -> tuple[KpiSource, ...]:
        return tuple(self.bindings)

    def get(self, source: KpiSource) -> KpiSourceBinding:
        if not isinstance(source, KpiSource):
            raise TypeError('source must be KpiSource')
        try:
            return self.bindings[source]
        except KeyError as error:
            raise KpiSourceBindingError(f'{source.value}: source has no registered binding') from error

    def get_view(self, view: KpiSourceView) -> tuple[KpiSourceBinding, KpiPartitionBinding]:
        if not isinstance(view, KpiSourceView):
            raise TypeError('view must be KpiSourceView')
        binding = self.get(view.source)
        return binding, binding.get_partition(view.partition)


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return _required_text(value, 'optional text')


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value.strip()
