from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from ada.kpis.core import KpiSource
from ada.kpis.sources.errors import KpiSourceBindingError
from atlanticus.datasets.errors import DatasetTargetError
from atlanticus.datasets.models import DatasetDefinition


class TimePartitionGranularity(StrEnum):
    DAY = 'day'
    MONTH = 'month'


@dataclass(frozen=True, slots=True)
class KpiSourceBinding:
    source: KpiSource
    definition: DatasetDefinition
    snapshot_materialization: str | None = None
    time_materialization: str | None = None
    time_partition_granularity: TimePartitionGranularity | None = None
    shift_materialization: str | None = None
    timestamp_column: str | None = None
    shift_column: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, KpiSource):
            raise TypeError('source must be KpiSource')
        if not isinstance(self.definition, DatasetDefinition):
            raise TypeError(f'{self.source.value}: definition must be DatasetDefinition')
        snapshot = _optional_text(self.snapshot_materialization)
        time = _optional_text(self.time_materialization)
        shift = _optional_text(self.shift_materialization)
        timestamp = _optional_text(self.timestamp_column)
        shift_column = _optional_text(self.shift_column)
        object.__setattr__(self, 'snapshot_materialization', snapshot)
        object.__setattr__(self, 'time_materialization', time)
        object.__setattr__(self, 'shift_materialization', shift)
        object.__setattr__(self, 'timestamp_column', timestamp)
        object.__setattr__(self, 'shift_column', shift_column)
        if snapshot is None and time is None and shift is None:
            raise KpiSourceBindingError(
                f'{self.source.value}: binding requires at least one materialization'
            )
        for materialization in (snapshot, time, shift):
            if materialization is None:
                continue
            try:
                self.definition.get_materialization(materialization)
            except DatasetTargetError as error:
                raise KpiSourceBindingError(
                    f'{self.source.value}: unknown materialization: {materialization}'
                ) from error
        if time is not None:
            if not isinstance(self.time_partition_granularity, TimePartitionGranularity):
                raise KpiSourceBindingError(
                    f'{self.source.value}: time materialization requires partition granularity'
                )
            if timestamp is None:
                raise KpiSourceBindingError(
                    f'{self.source.value}: time materialization requires timestamp_column'
                )
        elif self.time_partition_granularity is not None:
            raise KpiSourceBindingError(
                f'{self.source.value}: time partition granularity requires time materialization'
            )
        if shift is not None and shift_column is None:
            raise KpiSourceBindingError(
                f'{self.source.value}: shift materialization requires shift_column'
            )
        if shift is None and shift_column is not None:
            raise KpiSourceBindingError(
                f'{self.source.value}: shift_column requires shift materialization'
            )


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
            raise KpiSourceBindingError(
                f'{source.value}: source has no registered binding'
            ) from error


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError('optional text values must be non-empty strings or None')
    return value.strip()
