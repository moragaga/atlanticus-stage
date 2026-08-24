# Espejo pedagógico de bindings, routing y carga física de datos operacionales.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from ada.data.core import DataPartition, DataSource, DataSourceView
from ada.data.sources.errors import DataSourceBindingError
from atlanticus.datasets.errors import DatasetTargetError
from atlanticus.datasets.models import DatasetDefinition


class TimePartitionGranularity(StrEnum):
    DAY = 'day'
    MONTH = 'month'


@dataclass(frozen=True, slots=True)
class DataPartitionBinding:
    partition: DataPartition
    materialization: str
    time_partition_granularity: TimePartitionGranularity | None = None
    timestamp_column: str | None = None
    shift_column: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.partition, DataPartition):
            raise TypeError('partition must be DataPartition')
        materialization = _required_text(self.materialization, 'materialization')
        timestamp = _optional_text(self.timestamp_column)
        shift_column = _optional_text(self.shift_column)
        if self.time_partition_granularity is not None and not isinstance(
            self.time_partition_granularity, TimePartitionGranularity
        ):
            raise TypeError('time_partition_granularity must be TimePartitionGranularity or None')
        if self.time_partition_granularity is not None and timestamp is None:
            raise DataSourceBindingError(
                f'{self.partition.value}: partitioned time binding requires timestamp_column'
            )
        if shift_column is not None and self.time_partition_granularity is not None:
            raise DataSourceBindingError(
                f'{self.partition.value}: shift binding cannot use time partition granularity'
            )
        object.__setattr__(self, 'materialization', materialization)
        object.__setattr__(self, 'timestamp_column', timestamp)
        object.__setattr__(self, 'shift_column', shift_column)


@dataclass(frozen=True, slots=True)
class DataSourceBinding:
    source: DataSource
    definition: DatasetDefinition
    partitions: Mapping[DataPartition, DataPartitionBinding]

    def __post_init__(self) -> None:
        if not isinstance(self.source, DataSource):
            raise TypeError('source must be DataSource')
        if not isinstance(self.definition, DatasetDefinition):
            raise TypeError(f'{self.source.value}: definition must be DatasetDefinition')
        if not isinstance(self.partitions, Mapping) or not self.partitions:
            raise DataSourceBindingError(f'{self.source.value}: binding requires partitions')
        normalized: dict[DataPartition, DataPartitionBinding] = {}
        for key, partition in self.partitions.items():
            if not isinstance(key, DataPartition):
                raise TypeError(f'{self.source.value}: partition keys must be DataPartition values')
            if not isinstance(partition, DataPartitionBinding):
                raise TypeError(f'{self.source.value}/{key.value}: invalid partition binding')
            if partition.partition is not key:
                raise DataSourceBindingError(
                    f'{self.source.value}/{key.value}: partition key and binding must match'
                )
            try:
                self.definition.get_materialization(partition.materialization)
            except DatasetTargetError as error:
                raise DataSourceBindingError(
                    f'{self.source.value}/{key.value}: unknown materialization: '
                    f'{partition.materialization}'
                ) from error
            normalized[key] = partition
        object.__setattr__(self, 'partitions', MappingProxyType(normalized))

    def get_partition(self, partition: DataPartition) -> DataPartitionBinding:
        if not isinstance(partition, DataPartition):
            raise TypeError('partition must be DataPartition')
        try:
            return self.partitions[partition]
        except KeyError as error:
            raise DataSourceBindingError(
                f'{self.source.value}: source does not support partition: {partition.value}'
            ) from error


@dataclass(frozen=True, slots=True)
class DataSourceRegistry:
    bindings: Mapping[DataSource, DataSourceBinding]

    def __post_init__(self) -> None:
        if not isinstance(self.bindings, Mapping):
            raise TypeError('bindings must be a mapping')
        normalized: dict[DataSource, DataSourceBinding] = {}
        for source, binding in self.bindings.items():
            if not isinstance(source, DataSource):
                raise TypeError('registry keys must be DataSource values')
            if not isinstance(binding, DataSourceBinding):
                raise TypeError(f'{source.value}: binding must be DataSourceBinding')
            if binding.source is not source:
                raise DataSourceBindingError(
                    f'{source.value}: registry key and binding source must match'
                )
            normalized[source] = binding
        object.__setattr__(self, 'bindings', MappingProxyType(normalized))

    @property
    def sources(self) -> tuple[DataSource, ...]:
        return tuple(self.bindings)

    def get(self, source: DataSource) -> DataSourceBinding:
        if not isinstance(source, DataSource):
            raise TypeError('source must be DataSource')
        try:
            return self.bindings[source]
        except KeyError as error:
            raise DataSourceBindingError(
                f'{source.value}: source has no registered binding'
            ) from error

    def get_view(self, view: DataSourceView) -> tuple[DataSourceBinding, DataPartitionBinding]:
        if not isinstance(view, DataSourceView):
            raise TypeError('view must be DataSourceView')
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
