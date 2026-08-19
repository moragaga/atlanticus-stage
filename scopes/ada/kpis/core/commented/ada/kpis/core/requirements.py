# Declara requerimientos exactos de datos por KPI.
# TimeWindow representa deltas cronológicos; ShiftSelection representa ventanas operacionales basadas en shift_id.
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from ada.kpis.core.enums import ShiftScope


class KpiTimeWindowUnit(StrEnum):
    MINUTES = 'minutes'
    HOURS = 'hours'
    DAYS = 'days'


@dataclass(frozen=True, slots=True)
class KpiTimeWindow:
    value: int
    unit: KpiTimeWindowUnit

    def __post_init__(self) -> None:
        if not isinstance(self.value, int) or isinstance(self.value, bool) or self.value <= 0:
            raise ValueError('time window value must be an integer greater than zero')
        if not isinstance(self.unit, KpiTimeWindowUnit):
            raise TypeError('time window unit must be KpiTimeWindowUnit')

    def to_timedelta(self) -> timedelta:
        if self.unit is KpiTimeWindowUnit.MINUTES:
            return timedelta(minutes=self.value)
        if self.unit is KpiTimeWindowUnit.HOURS:
            return timedelta(hours=self.value)
        return timedelta(days=self.value)


@dataclass(frozen=True, slots=True)
class ShiftSelection:
    scope: ShiftScope
    days: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ShiftScope):
            raise TypeError('shift scope must be ShiftScope')
        if self.scope is ShiftScope.DAYS:
            if not isinstance(self.days, int) or isinstance(self.days, bool):
                raise ValueError('days shift scope requires an integer days value')
            if not 1 <= self.days <= 7:
                raise ValueError('days shift scope requires days between 1 and 7')
            return
        if self.days is not None:
            raise ValueError('days can only be declared with ShiftScope.DAYS')


@dataclass(frozen=True, slots=True)
class SourceRequirement:
    columns: tuple[str, ...]
    time_window: KpiTimeWindow | None = None
    shift: ShiftSelection | None = None

    def __post_init__(self) -> None:
        columns = tuple(_required_text(column, 'column') for column in self.columns)
        if not columns:
            raise ValueError('source requirement requires at least one column')
        if len(columns) != len(set(columns)):
            raise ValueError('source requirement columns must be unique')
        if self.time_window is not None and not isinstance(self.time_window, KpiTimeWindow):
            raise TypeError('time_window must be KpiTimeWindow')
        if self.shift is not None and not isinstance(self.shift, ShiftSelection):
            raise TypeError('shift must be ShiftSelection')
        if self.time_window is not None and self.shift is not None:
            raise ValueError('source requirement cannot mix time_window and shift')
        object.__setattr__(self, 'columns', columns)


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value.strip()
