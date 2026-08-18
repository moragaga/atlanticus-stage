from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class WorkShiftCode(StrEnum):
    DAY = 'day'
    NIGHT = 'night'


@dataclass(frozen=True, slots=True)
class OperationalDayWindow:
    calendar: str
    operational_date: date
    start_local: datetime
    end_local: datetime
    start_utc: datetime
    end_utc: datetime


@dataclass(frozen=True, slots=True)
class WorkShiftWindow:
    calendar: str
    code: WorkShiftCode
    turn: str
    operational_date: date
    start_local: datetime
    end_local: datetime
    start_utc: datetime
    end_utc: datetime


@dataclass(frozen=True, slots=True)
class OperationalWeekPartition:
    calendar: str
    operational_year: str
    operational_week: str

    def __post_init__(self) -> None:
        for field_name in ('calendar', 'operational_year', 'operational_week'):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{field_name} must be a non-empty string')
            object.__setattr__(self, field_name, value.strip())

    def as_mapping(self) -> dict[str, str]:
        return {
            'calendar': self.calendar,
            'operational_year': self.operational_year,
            'operational_week': self.operational_week,
        }


@dataclass(frozen=True, slots=True)
class OperationalWeekWindow:
    partition: OperationalWeekPartition
    start_operational_date: date
    end_operational_date: date
    start_local: datetime
    end_local: datetime
    start_utc: datetime
    end_utc: datetime


@dataclass(frozen=True, slots=True)
class ShiftIdTurn:
    shift_id: int
    shift_suffix: str
    nominal_date: date
    shift_start_local: datetime
    shift_end_local: datetime
    shift_start_utc: datetime
    shift_end_utc: datetime

    @property
    def partition(self) -> dict[str, str]:
        return {
            'year': f'{self.nominal_date.year:04d}',
            'month': f'{self.nominal_date.month:02d}',
            'day': f'{self.nominal_date.day:02d}',
            'turn': self.shift_suffix,
        }
