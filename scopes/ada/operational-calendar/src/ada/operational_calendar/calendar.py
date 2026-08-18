from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ada.operational_calendar.models import (
    OperationalDayWindow,
    OperationalWeekPartition,
    OperationalWeekWindow,
    WorkShiftCode,
    WorkShiftWindow,
)

DEFAULT_TIMEZONE_NAME = 'America/Santiago'
DEFAULT_OPERATIONAL_WEEK_START_WEEKDAY = 4
NIGHT_TURN = '001'
DAY_TURN = '002'


@dataclass(frozen=True, slots=True)
class OperationalCalendar:
    key: str
    operational_day_start_hour: int
    day_shift_start_hour: int
    week_start_weekday: int = DEFAULT_OPERATIONAL_WEEK_START_WEEKDAY
    timezone_name: str = DEFAULT_TIMEZONE_NAME

    def __post_init__(self) -> None:
        key = _required_text(self.key, 'key')
        timezone_name = _required_text(self.timezone_name, 'timezone_name')
        _validate_hour(self.operational_day_start_hour, 'operational_day_start_hour')
        _validate_hour(self.day_shift_start_hour, 'day_shift_start_hour')
        if self.operational_day_start_hour <= self.day_shift_start_hour:
            raise ValueError('operational_day_start_hour must be greater than day_shift_start_hour')
        if not isinstance(self.week_start_weekday, int) or isinstance(
            self.week_start_weekday, bool
        ):
            raise ValueError('week_start_weekday must be an integer')
        if not 0 <= self.week_start_weekday <= 6:
            raise ValueError('week_start_weekday must be between 0 and 6')
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as error:
            raise ValueError('timezone_name is not available') from error
        object.__setattr__(self, 'key', key)
        object.__setattr__(self, 'timezone_name', timezone_name)

    def resolve(self, timestamp_utc: datetime) -> OperationalWeekPartition:
        return self.resolve_operational_week(timestamp_utc).partition

    def resolve_operational_day(self, value: datetime | None = None) -> OperationalDayWindow:
        local_value = self._to_local(value)
        operational_date = local_value.date()
        if local_value.time() >= time(hour=self.operational_day_start_hour):
            operational_date += timedelta(days=1)
        start_local = self._boundary(operational_date - timedelta(days=1))
        end_local = self._boundary(operational_date)
        return OperationalDayWindow(
            calendar=self.key,
            operational_date=operational_date,
            start_local=start_local,
            end_local=end_local,
            start_utc=start_local.astimezone(UTC),
            end_utc=end_local.astimezone(UTC),
        )

    def resolve_work_shift(self, value: datetime | None = None) -> WorkShiftWindow:
        local_value = self._to_local(value)
        local_date = local_value.date()
        local_time = local_value.time()
        if local_time >= time(hour=self.operational_day_start_hour):
            return self._build_work_shift(
                code=WorkShiftCode.NIGHT,
                operational_date=local_date + timedelta(days=1),
            )
        if local_time >= time(hour=self.day_shift_start_hour):
            return self._build_work_shift(
                code=WorkShiftCode.DAY,
                operational_date=local_date,
            )
        return self._build_work_shift(
            code=WorkShiftCode.NIGHT,
            operational_date=local_date,
        )

    def resolve_previous_work_shift(self, value: datetime | None = None) -> WorkShiftWindow:
        current = self.resolve_work_shift(value)
        return self.resolve_work_shift(current.start_utc - timedelta(microseconds=1))

    def resolve_operational_week(self, value: datetime | None = None) -> OperationalWeekWindow:
        operational_date = self.resolve_operational_day(value).operational_date
        days_since_start = (operational_date.weekday() - self.week_start_weekday) % 7
        start_operational_date = operational_date - timedelta(days=days_since_start)
        end_operational_date = start_operational_date + timedelta(days=7)
        start_local = self._boundary(start_operational_date - timedelta(days=1))
        end_local = self._boundary(end_operational_date - timedelta(days=1))
        iso_year, iso_week, _ = start_operational_date.isocalendar()
        return OperationalWeekWindow(
            partition=OperationalWeekPartition(
                calendar=self.key,
                operational_year=f'{iso_year:04d}',
                operational_week=f'W{iso_week:02d}',
            ),
            start_operational_date=start_operational_date,
            end_operational_date=end_operational_date,
            start_local=start_local,
            end_local=end_local,
            start_utc=start_local.astimezone(UTC),
            end_utc=end_local.astimezone(UTC),
        )

    def build_work_shifts_for_week(
        self,
        value: datetime | None = None,
    ) -> tuple[WorkShiftWindow, ...]:
        week = self.resolve_operational_week(value)
        shifts: list[WorkShiftWindow] = []
        for day_offset in range(7):
            operational_date = week.start_operational_date + timedelta(days=day_offset)
            shifts.extend(
                (
                    self._build_work_shift(
                        code=WorkShiftCode.NIGHT,
                        operational_date=operational_date,
                    ),
                    self._build_work_shift(
                        code=WorkShiftCode.DAY,
                        operational_date=operational_date,
                    ),
                )
            )
        return tuple(shifts)

    def _build_work_shift(
        self,
        *,
        code: WorkShiftCode,
        operational_date: date,
    ) -> WorkShiftWindow:
        timezone_value = ZoneInfo(self.timezone_name)
        if code is WorkShiftCode.NIGHT:
            start_local = datetime.combine(
                operational_date - timedelta(days=1),
                time(hour=self.operational_day_start_hour),
                tzinfo=timezone_value,
            )
            end_local = datetime.combine(
                operational_date,
                time(hour=self.day_shift_start_hour),
                tzinfo=timezone_value,
            )
            turn = NIGHT_TURN
        else:
            start_local = datetime.combine(
                operational_date,
                time(hour=self.day_shift_start_hour),
                tzinfo=timezone_value,
            )
            end_local = datetime.combine(
                operational_date,
                time(hour=self.operational_day_start_hour),
                tzinfo=timezone_value,
            )
            turn = DAY_TURN
        return WorkShiftWindow(
            calendar=self.key,
            code=code,
            turn=turn,
            operational_date=operational_date,
            start_local=start_local,
            end_local=end_local,
            start_utc=start_local.astimezone(UTC),
            end_utc=end_local.astimezone(UTC),
        )

    def _boundary(self, value_date: date) -> datetime:
        return datetime.combine(
            value_date,
            time(hour=self.operational_day_start_hour),
            tzinfo=ZoneInfo(self.timezone_name),
        )

    def _to_local(self, value: datetime | None) -> datetime:
        resolved = datetime.now(UTC) if value is None else value
        if not isinstance(resolved, datetime):
            raise TypeError('value must be a datetime or None')
        if resolved.tzinfo is None or resolved.utcoffset() is None:
            raise ValueError('value must be timezone-aware')
        return resolved.astimezone(ZoneInfo(self.timezone_name))


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value.strip()


def _validate_hour(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f'{field_name} must be an integer')
    if not 0 <= value <= 23:
        raise ValueError(f'{field_name} must be between 0 and 23')


PLANT_CALENDAR = OperationalCalendar(
    key='plant_19',
    operational_day_start_hour=19,
    day_shift_start_hour=7,
)

MINE_CALENDAR = OperationalCalendar(
    key='mine_21',
    operational_day_start_hour=21,
    day_shift_start_hour=9,
)

ADA_OPERATIONAL_CALENDARS = {
    PLANT_CALENDAR.key: PLANT_CALENDAR,
    MINE_CALENDAR.key: MINE_CALENDAR,
}
