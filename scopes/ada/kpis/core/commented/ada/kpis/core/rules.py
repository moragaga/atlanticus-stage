# Valida KpiSpec y permite múltiples vistas del mismo source cuando cambian de partición.
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ada.kpis.core.enums import (
    KpiArea,
    KpiMode,
    KpiOperationalScope,
    KpiPartition,
    KpiSource,
    KpiValueKind,
)
from ada.kpis.core.requirements import KpiTimeWindow, ShiftSelection, SourceRequirement
from ada.kpis.core.runtime import DataRuntimeContext
from ada.kpis.core.values import KpiNativeValue

KpiResolver = Callable[[DataRuntimeContext], KpiNativeValue]
OverKpiResolver = Callable[[Mapping[str, KpiNativeValue]], KpiNativeValue]

_SINGLE_COLUMN_MODES = frozenset({KpiMode.LATEST, KpiMode.LATEST_NUMBER, KpiMode.STATUS})
_MULTI_COLUMN_MODES = frozenset({KpiMode.SUM_LATESTS_NUMBERS, KpiMode.MAX_LATESTS_NUMBERS})


@dataclass(frozen=True, slots=True)
class KpiSpec:
    key: str
    area: KpiArea
    mode: KpiMode
    source: KpiSource | None = None
    partition: KpiPartition | None = None
    columns: tuple[str, ...] = ()
    source_requirements: tuple[SourceRequirement, ...] = ()
    decimals: int | None = None
    is_truncated: bool = True
    value_kind: KpiValueKind = KpiValueKind.VALUE
    persist_history: bool = False
    time_window: KpiTimeWindow | None = None
    operational_scope: KpiOperationalScope | None = None
    shift: ShiftSelection | None = None
    constant_value: KpiNativeValue = None
    custom_resolver: KpiResolver | None = None

    def __post_init__(self) -> None:
        key = _required_text(self.key, 'key')
        _require_enum(self.area, KpiArea, 'area')
        _require_enum(self.mode, KpiMode, 'mode')
        _require_enum(self.value_kind, KpiValueKind, 'value_kind')
        if self.source is not None:
            _require_enum(self.source, KpiSource, 'source')
        if self.partition is not None:
            _require_enum(self.partition, KpiPartition, 'partition')
        columns = tuple(_required_text(column, 'column') for column in self.columns)
        if len(columns) != len(set(columns)):
            raise ValueError(f'{key}: columns must be unique')
        requirements = tuple(self.source_requirements)
        if not all(isinstance(item, SourceRequirement) for item in requirements):
            raise TypeError(f'{key}: source_requirements must contain SourceRequirement values')
        views = tuple(item.view for item in requirements)
        if len(views) != len(set(views)):
            raise ValueError(f'{key}: source_requirements must be unique by source and partition')
        _validate_decimals(self.decimals, key)
        _validate_boolean(self.is_truncated, 'is_truncated', key)
        _validate_boolean(self.persist_history, 'persist_history', key)
        if self.time_window is not None and not isinstance(self.time_window, KpiTimeWindow):
            raise TypeError(f'{key}: time_window must be KpiTimeWindow')
        if self.operational_scope is not None and not isinstance(
            self.operational_scope, KpiOperationalScope
        ):
            raise TypeError(f'{key}: operational_scope must be KpiOperationalScope')
        if self.shift is not None and not isinstance(self.shift, ShiftSelection):
            raise TypeError(f'{key}: shift must be ShiftSelection')

        object.__setattr__(self, 'key', key)
        object.__setattr__(self, 'columns', columns)
        object.__setattr__(self, 'source_requirements', requirements)

        if self.mode is KpiMode.CONSTANT:
            self._validate_constant()
            return
        if self.mode is KpiMode.CUSTOM:
            self._validate_custom()
            return
        self._validate_simple_mode()

    @property
    def requirements(self) -> tuple[SourceRequirement, ...]:
        if self.source_requirements:
            return self.source_requirements
        if self.source is None:
            return ()
        assert self.partition is not None
        return (
            SourceRequirement(
                source=self.source,
                partition=self.partition,
                columns=self.columns,
                time_window=self.time_window,
                operational_scope=self.operational_scope,
                shift=self.shift,
            ),
        )

    def _validate_constant(self) -> None:
        if self.source is not None or self.partition is not None:
            raise ValueError(f'{self.key}: constant must not declare source or partition')
        if self.columns:
            raise ValueError(f'{self.key}: constant must not declare columns')
        if self.source_requirements:
            raise ValueError(f'{self.key}: constant must not declare source_requirements')
        if any(
            value is not None for value in (self.time_window, self.operational_scope, self.shift)
        ):
            raise ValueError(f'{self.key}: constant must not declare a temporal selector')
        if self.custom_resolver is not None:
            raise ValueError(f'{self.key}: constant must not declare custom_resolver')

    def _validate_custom(self) -> None:
        if self.custom_resolver is None or not callable(self.custom_resolver):
            raise ValueError(f'{self.key}: custom requires custom_resolver')
        has_simple_source = self.source is not None or self.partition is not None
        has_requirements = bool(self.source_requirements)
        if has_simple_source and has_requirements:
            raise ValueError(f'{self.key}: custom cannot mix source and source_requirements')
        if not has_simple_source and not has_requirements:
            raise ValueError(
                f'{self.key}: custom requires source+partition+columns or source_requirements'
            )
        if has_requirements:
            if self.columns:
                raise ValueError(f'{self.key}: custom with source_requirements must not declare columns')
            if any(
                value is not None
                for value in (self.time_window, self.operational_scope, self.shift)
            ):
                raise ValueError(
                    f'{self.key}: custom with source_requirements must not declare a temporal selector'
                )
            return
        self._require_simple_source()
        if not self.columns:
            raise ValueError(f'{self.key}: custom with source requires columns')
        self.requirements

    def _validate_simple_mode(self) -> None:
        if self.source_requirements:
            raise ValueError(f'{self.key}: source_requirements can only be used with custom')
        if self.custom_resolver is not None:
            raise ValueError(f'{self.key}: {self.mode.value} must not declare custom_resolver')
        self._require_simple_source()
        if self.mode in _SINGLE_COLUMN_MODES and len(self.columns) != 1:
            raise ValueError(f'{self.key}: {self.mode.value} requires exactly one column')
        if self.mode in _MULTI_COLUMN_MODES and not self.columns:
            raise ValueError(f'{self.key}: {self.mode.value} requires at least one column')
        self.requirements

    def _require_simple_source(self) -> None:
        if self.source is None:
            raise ValueError(f'{self.key}: {self.mode.value} requires source')
        if self.partition is None:
            raise ValueError(f'{self.key}: {self.mode.value} requires partition')


@dataclass(frozen=True, slots=True)
class OverKpiSpec:
    key: str
    area: KpiArea
    dependencies: tuple[str, ...]
    resolver: OverKpiResolver
    decimals: int = 2
    is_truncated: bool = True
    value_kind: KpiValueKind = KpiValueKind.VALUE
    persist_history: bool = False

    def __post_init__(self) -> None:
        key = _required_text(self.key, 'key')
        _require_enum(self.area, KpiArea, 'area')
        _require_enum(self.value_kind, KpiValueKind, 'value_kind')
        dependencies = tuple(_required_text(value, 'dependency') for value in self.dependencies)
        if not dependencies:
            raise ValueError(f'{key}: over kpi requires dependencies')
        if len(dependencies) != len(set(dependencies)):
            raise ValueError(f'{key}: over kpi dependencies must be unique')
        if key in dependencies:
            raise ValueError(f'{key}: over kpi cannot depend on itself')
        if not callable(self.resolver):
            raise ValueError(f'{key}: over kpi resolver must be callable')
        _validate_decimals(self.decimals, key, allow_none=False)
        _validate_boolean(self.is_truncated, 'is_truncated', key)
        _validate_boolean(self.persist_history, 'persist_history', key)
        object.__setattr__(self, 'key', key)
        object.__setattr__(self, 'dependencies', dependencies)


def _validate_decimals(value: int | None, key: str, *, allow_none: bool = True) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f'{key}: decimals must be an integer greater than or equal to zero')


def _validate_boolean(value: bool, field_name: str, key: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f'{key}: {field_name} must be boolean')


def _require_enum(value: object, expected_type: type, field_name: str) -> None:
    if not isinstance(value, expected_type):
        raise TypeError(f'{field_name} must be {expected_type.__name__}')


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value.strip()
