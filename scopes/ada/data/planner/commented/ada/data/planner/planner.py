# Espejo pedagógico del planner compartido de datos operacionales.
# El planner fusiona columnas sólo cuando nombre y tipo son compatibles entre consumidores.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ada.data.core import (
    DataColumn,
    DataPartition,
    DataRequirement,
    DataSource,
    DataSourceView,
    OperationalScope,
    ShiftSelection,
    TimeWindow,
)
from ada.data.planner.errors import DataPlanKeyError, DataPlanSchemaError


@dataclass(frozen=True, slots=True)
class DataSourceViewLoadPlan:
    view: DataSourceView
    columns: tuple[DataColumn, ...]
    time_windows: tuple[TimeWindow, ...] = ()
    operational_scopes: tuple[OperationalScope, ...] = ()
    shifts: tuple[ShiftSelection, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.view, DataSourceView):
            raise TypeError('view must be DataSourceView')
        columns = _normalize_columns(self.columns)
        if not columns:
            raise ValueError('source view load plan requires columns')
        time_windows = tuple(self.time_windows)
        operational_scopes = tuple(self.operational_scopes)
        shifts = tuple(self.shifts)
        if not all(isinstance(item, TimeWindow) for item in time_windows):
            raise TypeError('time_windows must contain TimeWindow values')
        if not all(isinstance(item, OperationalScope) for item in operational_scopes):
            raise TypeError('operational_scopes must contain OperationalScope values')
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
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def source(self) -> DataSource:
        return self.view.source

    @property
    def partition(self) -> DataPartition:
        return self.view.partition


@dataclass(frozen=True, slots=True)
class DataLoadPlan:
    views: tuple[DataSourceViewLoadPlan, ...]
    requirements_by_key: Mapping[str, tuple[DataRequirement, ...]]

    def __post_init__(self) -> None:
        views = tuple(self.views)
        if not all(isinstance(item, DataSourceViewLoadPlan) for item in views):
            raise TypeError('views must contain DataSourceViewLoadPlan values')
        view_keys = tuple(item.view for item in views)
        if len(view_keys) != len(set(view_keys)):
            raise ValueError('source view load plans must be unique by source and partition')
        normalized: dict[str, tuple[DataRequirement, ...]] = {}
        for key, requirements in self.requirements_by_key.items():
            normalized_key = _required_text(key, 'consumer key')
            copied = tuple(requirements)
            if not all(isinstance(item, DataRequirement) for item in copied):
                raise TypeError(
                    f'{normalized_key}: requirements must contain DataRequirement values'
                )
            requirement_views = tuple(item.view for item in copied)
            if len(requirement_views) != len(set(requirement_views)):
                raise ValueError(
                    f'{normalized_key}: requirements must be unique by source and partition'
                )
            normalized[normalized_key] = copied
        object.__setattr__(self, 'views', views)
        object.__setattr__(self, 'requirements_by_key', MappingProxyType(normalized))

    @property
    def sources(self) -> tuple[DataSource, ...]:
        return tuple(dict.fromkeys(plan.source for plan in self.views))

    def view_plan(self, source, partition) -> DataSourceViewLoadPlan:
        view = DataSourceView(source=source, partition=partition)
        for plan in self.views:
            if plan.view == view:
                return plan
        raise DataPlanKeyError(
            f'{source.value}/{partition.value}: view is not part of this load plan'
        )

    def requirements_for(self, key: str) -> tuple[DataRequirement, ...]:
        normalized = _required_text(key, 'consumer key')
        try:
            return self.requirements_by_key[normalized]
        except KeyError as error:
            raise DataPlanKeyError(
                f'{normalized}: consumer is not part of this load plan'
            ) from error


class DataRequirementPlanner:
    def plan(
        self,
        requirements_by_key: Mapping[str, tuple[DataRequirement, ...]],
    ) -> DataLoadPlan:
        if not isinstance(requirements_by_key, Mapping):
            raise TypeError('requirements_by_key must be a mapping')
        normalized: dict[str, tuple[DataRequirement, ...]] = {}
        grouped: dict[DataSourceView, list[DataRequirement]] = {}
        view_order: list[DataSourceView] = []
        for key, requirements in requirements_by_key.items():
            normalized_key = _required_text(key, 'consumer key')
            copied = tuple(requirements)
            if not all(isinstance(item, DataRequirement) for item in copied):
                raise TypeError(
                    f'{normalized_key}: requirements must contain DataRequirement values'
                )
            requirement_views = tuple(item.view for item in copied)
            if len(requirement_views) != len(set(requirement_views)):
                raise ValueError(
                    f'{normalized_key}: requirements must be unique by source and partition'
                )
            normalized[normalized_key] = copied
            for requirement in copied:
                view = requirement.view
                if view not in grouped:
                    grouped[view] = []
                    view_order.append(view)
                grouped[view].append(requirement)
        views = tuple(
            _merge_view_requirements(view=view, requirements=grouped[view]) for view in view_order
        )
        return DataLoadPlan(views=views, requirements_by_key=normalized)


def _merge_view_requirements(
    *,
    view: DataSourceView,
    requirements: list[DataRequirement],
) -> DataSourceViewLoadPlan:
    columns: list[DataColumn] = []
    time_windows = []
    operational_scopes = []
    shifts = []
    for requirement in requirements:
        _extend_unique_columns(columns, requirement.columns, view=view)
        if requirement.time_window is not None and requirement.time_window not in time_windows:
            time_windows.append(requirement.time_window)
        if (
            requirement.operational_scope is not None
            and requirement.operational_scope not in operational_scopes
        ):
            operational_scopes.append(requirement.operational_scope)
        if requirement.shift is not None and requirement.shift not in shifts:
            shifts.append(requirement.shift)
    return DataSourceViewLoadPlan(
        view=view,
        columns=tuple(columns),
        time_windows=tuple(time_windows),
        operational_scopes=tuple(operational_scopes),
        shifts=tuple(shifts),
    )


def _extend_unique_columns(
    target: list[DataColumn],
    values: tuple[DataColumn, ...],
    *,
    view: DataSourceView,
) -> None:
    by_name = {column.name: column for column in target}
    for column in values:
        existing = by_name.get(column.name)
        if existing is None:
            target.append(column)
            by_name[column.name] = column
            continue
        if existing.data_type is not column.data_type:
            raise DataPlanSchemaError(
                f'{view.source.value}/{view.partition.value}: conflicting data types for '
                f'column {column.name}: {existing.data_type.value} != {column.data_type.value}'
            )


def _normalize_columns(values: tuple[DataColumn, ...]) -> tuple[DataColumn, ...]:
    columns = tuple(values)
    if not all(isinstance(column, DataColumn) for column in columns):
        raise TypeError('columns must contain DataColumn values')
    names = tuple(column.name for column in columns)
    if len(names) != len(set(names)):
        raise ValueError('column names must not contain duplicates')
    return columns


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value.strip()
