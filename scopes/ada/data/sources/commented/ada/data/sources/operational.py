# Espejo pedagógico de bindings, routing y carga física de datos operacionales.
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ada.data.core import OperationalScope, normalize_utc_second
from ada.operational_calendar import MINE_CALENDAR, PLANT_CALENDAR, OperationalCalendar


@dataclass(frozen=True, slots=True)
class OperationalWindow:
    start_utc: datetime
    end_utc: datetime


@dataclass(frozen=True, slots=True)
class OperationalWindowResolver:
    def resolve(
        self,
        *,
        scope: OperationalScope,
        as_of: datetime,
    ) -> OperationalWindow:
        if not isinstance(scope, OperationalScope):
            raise TypeError('scope must be OperationalScope')
        value = normalize_utc_second(as_of, field_name='as_of')
        calendar = _calendar_for(scope)
        if scope in {
            OperationalScope.CURRENT_TURN_MINE,
            OperationalScope.CURRENT_TURN_PLANT,
        }:
            window = calendar.resolve_work_shift(value)
            return OperationalWindow(window.start_utc, min(value, window.end_utc))
        if scope in {
            OperationalScope.PREVIOUS_TURN_MINE,
            OperationalScope.PREVIOUS_TURN_PLANT,
        }:
            window = calendar.resolve_previous_work_shift(value)
            return OperationalWindow(window.start_utc, window.end_utc)
        if scope in {
            OperationalScope.CURRENT_OPERATIONAL_DAY_MINE,
            OperationalScope.CURRENT_OPERATIONAL_DAY_PLANT,
        }:
            window = calendar.resolve_operational_day(value)
            return OperationalWindow(window.start_utc, min(value, window.end_utc))
        return _operational_month(calendar=calendar, value=value)


def _calendar_for(scope: OperationalScope) -> OperationalCalendar:
    return MINE_CALENDAR if scope.value.endswith('_mine') else PLANT_CALENDAR


def _operational_month(
    *,
    calendar: OperationalCalendar,
    value: datetime,
) -> OperationalWindow:
    operational_date = calendar.resolve_operational_day(value).operational_date
    first_operational_date = date(operational_date.year, operational_date.month, 1)
    next_month_operational_date = _first_day_next_month(first_operational_date)
    timezone_value = ZoneInfo(calendar.timezone_name)
    start_local = datetime.combine(
        first_operational_date - timedelta(days=1),
        time(hour=calendar.operational_day_start_hour),
        tzinfo=timezone_value,
    )
    end_local = datetime.combine(
        next_month_operational_date - timedelta(days=1),
        time(hour=calendar.operational_day_start_hour),
        tzinfo=timezone_value,
    )
    return OperationalWindow(
        start_utc=start_local.astimezone(UTC),
        end_utc=min(value, end_local.astimezone(UTC)),
    )


def _first_day_next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)
