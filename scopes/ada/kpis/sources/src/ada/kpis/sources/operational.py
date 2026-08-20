from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ada.kpis.core import KpiOperationalScope, KpiWatermark
from ada.operational_calendar import MINE_CALENDAR, PLANT_CALENDAR, OperationalCalendar


@dataclass(frozen=True, slots=True)
class KpiOperationalWindow:
    start_utc: datetime
    end_utc: datetime


@dataclass(frozen=True, slots=True)
class KpiOperationalWindowResolver:
    def resolve(
        self,
        *,
        scope: KpiOperationalScope,
        watermark: KpiWatermark,
    ) -> KpiOperationalWindow:
        if not isinstance(scope, KpiOperationalScope):
            raise TypeError('scope must be KpiOperationalScope')
        if not isinstance(watermark, KpiWatermark):
            raise TypeError('watermark must be KpiWatermark')
        calendar = _calendar_for(scope)
        value = watermark.timestamp_utc
        if scope in {
            KpiOperationalScope.CURRENT_TURN_MINE,
            KpiOperationalScope.CURRENT_TURN_PLANT,
        }:
            window = calendar.resolve_work_shift(value)
            return KpiOperationalWindow(window.start_utc, min(value, window.end_utc))
        if scope in {
            KpiOperationalScope.PREVIOUS_TURN_MINE,
            KpiOperationalScope.PREVIOUS_TURN_PLANT,
        }:
            window = calendar.resolve_previous_work_shift(value)
            return KpiOperationalWindow(window.start_utc, window.end_utc)
        if scope in {
            KpiOperationalScope.CURRENT_OPERATIONAL_DAY_MINE,
            KpiOperationalScope.CURRENT_OPERATIONAL_DAY_PLANT,
        }:
            window = calendar.resolve_operational_day(value)
            return KpiOperationalWindow(window.start_utc, min(value, window.end_utc))
        return _operational_month(calendar=calendar, value=value)


def _calendar_for(scope: KpiOperationalScope) -> OperationalCalendar:
    return MINE_CALENDAR if scope.value.endswith('_mine') else PLANT_CALENDAR


def _operational_month(
    *,
    calendar: OperationalCalendar,
    value: datetime,
) -> KpiOperationalWindow:
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
    return KpiOperationalWindow(
        start_utc=start_local.astimezone(UTC),
        end_utc=min(value, end_local.astimezone(UTC)),
    )


def _first_day_next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)
