from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ada.kpis.core import (
    KpiOperationalScope,
    KpiPartition,
    KpiSource,
    KpiSourceView,
    KpiTimeWindow,
    ShiftSelection,
    SourceRequirement,
)
from ada.kpis.planner.errors import KpiPlanKeyError


@dataclass(frozen=True, slots=True)
class KpiSourceViewLoadPlan:
    view: KpiSourceView
    columns: tuple[str, ...]
    time_windows: tuple[KpiTimeWindow, ...] = ()
    operational_scopes: tuple[KpiOperationalScope, ...] = ()
    shifts: tuple[ShiftSelection, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.view, KpiSourceView):
            raise TypeError('view must be KpiSourceView')
        columns = _normalize_columns(self.columns)
        if not columns:
            raise ValueError('source view load plan requires columns')
        time_windows = tuple(self.time_windows)
        operational_scopes = tuple(self.operational_scopes)
        shifts = tuple(self.shifts)
        if not all(isinstance(item, KpiTimeWindow) for item in time_windows):
            raise TypeError('time_windows must contain KpiTimeWindow values')
        if not all(isinstance(item, KpiOperationalScope) for item in operational_scopes):
            raise TypeError('operational_scopes must contain KpiOperationalScope values')
        if not all(isinstance(item, ShiftSelection) for item in shifts):
            raise TypeError('shifts must contain ShiftSelection values')
        for name, values in (
            ('time_windows', time_windows),
            ('operational_scopes', operational_scopes),
            ('shifts', shifts),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f'{name} must not contain duplicates')
        object.__setattr__(self, 'columns', columns)
        object.__setattr__(self, 'time_windows', time_windows)
        object.__setattr__(self, 'operational_scopes', operational_scopes)
        object.__setattr__(self, 'shifts', shifts)

    @property
    def source(self) -> KpiSource:
        return self.view.source

    @property
    def partition(self) -> KpiPartition:
        return self.view.partition


@dataclass(frozen=True, slots=True)
class KpiLoadPlan:
    views: tuple[KpiSourceViewLoadPlan, ...]
    requirements_by_kpi: Mapping[str, tuple[SourceRequirement, ...]]

    def __post_init__(self) -> None:
        views = tuple(self.views)
        if not all(isinstance(item, KpiSourceViewLoadPlan) for item in views):
            raise TypeError('views must contain KpiSourceViewLoadPlan values')
        view_keys = tuple(item.view for item in views)
        if len(view_keys) != len(set(view_keys)):
            raise ValueError('source view load plans must be unique by source and partition')
        normalized: dict[str, tuple[SourceRequirement, ...]] = {}
        for key, requirements in self.requirements_by_kpi.items():
            normalized_key = _required_text(key, 'kpi key')
            copied = tuple(requirements)
            if not all(isinstance(item, SourceRequirement) for item in copied):
                raise TypeError(
                    f'{normalized_key}: requirements must contain SourceRequirement values'
                )
            requirement_views = tuple(item.view for item in copied)
            if len(requirement_views) != len(set(requirement_views)):
                raise ValueError(
                    f'{normalized_key}: requirements must be unique by source and partition'
                )
            normalized[normalized_key] = copied
        object.__setattr__(self, 'views', views)
        object.__setattr__(self, 'requirements_by_kpi', MappingProxyType(normalized))

    @property
    def sources(self) -> tuple[KpiSource, ...]:
        return tuple(dict.fromkeys(plan.source for plan in self.views))

    def view_plan(
        self,
        source: KpiSource,
        partition: KpiPartition,
    ) -> KpiSourceViewLoadPlan:
        view = KpiSourceView(source=source, partition=partition)
        for plan in self.views:
            if plan.view == view:
                return plan
        raise KpiPlanKeyError(
            f'{source.value}/{partition.value}: view is not part of this load plan'
        )

    def requirements_for(self, kpi_key: str) -> tuple[SourceRequirement, ...]:
        key = _required_text(kpi_key, 'kpi key')
        try:
            return self.requirements_by_kpi[key]
        except KeyError as error:
            raise KpiPlanKeyError(f'{key}: KPI is not part of this load plan') from error


def _normalize_columns(values: tuple[str, ...]) -> tuple[str, ...]:
    columns = tuple(_required_text(value, 'column') for value in values)
    if len(columns) != len(set(columns)):
        raise ValueError('columns must not contain duplicates')
    return columns


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value.strip()
