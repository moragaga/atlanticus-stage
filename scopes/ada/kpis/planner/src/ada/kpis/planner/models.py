from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ada.kpis.core import KpiSource, KpiTimeWindow, ShiftSelection, SourceRequirement
from ada.kpis.planner.errors import KpiPlanKeyError


@dataclass(frozen=True, slots=True)
class KpiSourceLoadPlan:
    source: KpiSource
    snapshot_columns: tuple[str, ...] = ()
    time_columns: tuple[str, ...] = ()
    time_window: KpiTimeWindow | None = None
    shift_columns: tuple[str, ...] = ()
    shifts: tuple[ShiftSelection, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source, KpiSource):
            raise TypeError('source must be KpiSource')
        for field_name in ('snapshot_columns', 'time_columns', 'shift_columns'):
            columns = _normalize_columns(getattr(self, field_name), field_name)
            object.__setattr__(self, field_name, columns)
        shifts = tuple(self.shifts)
        if not all(isinstance(item, ShiftSelection) for item in shifts):
            raise TypeError('shifts must contain ShiftSelection values')
        if len(shifts) != len(set(shifts)):
            raise ValueError('shifts must not contain duplicates')
        object.__setattr__(self, 'shifts', shifts)
        if self.time_columns and not isinstance(self.time_window, KpiTimeWindow):
            raise ValueError('time_columns require time_window')
        if not self.time_columns and self.time_window is not None:
            raise ValueError('time_window requires time_columns')
        if self.shift_columns and not shifts:
            raise ValueError('shift_columns require shifts')
        if not self.shift_columns and shifts:
            raise ValueError('shifts require shift_columns')
        if not self.snapshot_columns and not self.time_columns and not self.shift_columns:
            raise ValueError('source load plan requires at least one load group')

    @property
    def columns(self) -> tuple[str, ...]:
        return _ordered_union(self.snapshot_columns, self.time_columns, self.shift_columns)


@dataclass(frozen=True, slots=True)
class KpiLoadPlan:
    sources: tuple[KpiSourceLoadPlan, ...]
    requirements_by_kpi: Mapping[str, Mapping[KpiSource, SourceRequirement]]

    def __post_init__(self) -> None:
        sources = tuple(self.sources)
        if not all(isinstance(item, KpiSourceLoadPlan) for item in sources):
            raise TypeError('sources must contain KpiSourceLoadPlan values')
        source_keys = tuple(item.source for item in sources)
        if len(source_keys) != len(set(source_keys)):
            raise ValueError('source load plans must be unique by source')
        normalized: dict[str, Mapping[KpiSource, SourceRequirement]] = {}
        for key, requirements in self.requirements_by_kpi.items():
            normalized_key = _required_text(key, 'kpi key')
            if normalized_key in normalized:
                raise ValueError('requirements_by_kpi keys must be unique')
            if not isinstance(requirements, Mapping):
                raise TypeError(f'{normalized_key}: requirements must be a mapping')
            copied: dict[KpiSource, SourceRequirement] = {}
            for source, requirement in requirements.items():
                if not isinstance(source, KpiSource):
                    raise TypeError(f'{normalized_key}: requirement source must be KpiSource')
                if not isinstance(requirement, SourceRequirement):
                    raise TypeError(
                        f'{normalized_key}: requirement for {source.value} must be SourceRequirement'
                    )
                copied[source] = requirement
            normalized[normalized_key] = MappingProxyType(copied)
        object.__setattr__(self, 'sources', sources)
        object.__setattr__(self, 'requirements_by_kpi', MappingProxyType(normalized))

    def source_plan(self, source: KpiSource) -> KpiSourceLoadPlan:
        if not isinstance(source, KpiSource):
            raise TypeError('source must be KpiSource')
        for plan in self.sources:
            if plan.source is source:
                return plan
        raise KpiPlanKeyError(f'{source.value}: source is not part of this load plan')

    def requirements_for(self, kpi_key: str) -> Mapping[KpiSource, SourceRequirement]:
        key = _required_text(kpi_key, 'kpi key')
        try:
            return self.requirements_by_kpi[key]
        except KeyError as error:
            raise KpiPlanKeyError(f'{key}: KPI is not part of this load plan') from error


def _normalize_columns(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    columns = tuple(_required_text(value, 'column') for value in values)
    if len(columns) != len(set(columns)):
        raise ValueError(f'{field_name} must not contain duplicates')
    return columns


def _ordered_union(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for group in groups for value in group))


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value.strip()
