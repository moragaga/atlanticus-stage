from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from ada.operational_calendar import (
    ADA_OPERATIONAL_CALENDARS,
    MINE_CALENDAR,
    PLANT_CALENDAR,
    OperationalCalendar,
    WorkShiftCode,
)


def test_builtin_calendars_keep_validated_plant_and_mine_boundaries() -> None:
    assert PLANT_CALENDAR.key == 'plant_19'
    assert PLANT_CALENDAR.operational_day_start_hour == 19
    assert PLANT_CALENDAR.day_shift_start_hour == 7
    assert MINE_CALENDAR.key == 'mine_21'
    assert MINE_CALENDAR.operational_day_start_hour == 21
    assert MINE_CALENDAR.day_shift_start_hour == 9
    assert ADA_OPERATIONAL_CALENDARS == {
        'plant_19': PLANT_CALENDAR,
        'mine_21': MINE_CALENDAR,
    }


def test_plant_operational_day_changes_at_1900_local() -> None:
    timezone = ZoneInfo('America/Santiago')

    before = PLANT_CALENDAR.resolve_operational_day(
        datetime(2026, 8, 17, 18, 59, tzinfo=timezone)
    )
    boundary = PLANT_CALENDAR.resolve_operational_day(
        datetime(2026, 8, 17, 19, 0, tzinfo=timezone)
    )

    assert before.operational_date.isoformat() == '2026-08-17'
    assert before.start_local.isoformat() == '2026-08-16T19:00:00-04:00'
    assert before.end_local.isoformat() == '2026-08-17T19:00:00-04:00'
    assert boundary.operational_date.isoformat() == '2026-08-18'
    assert boundary.start_local.isoformat() == '2026-08-17T19:00:00-04:00'


def test_mine_operational_day_changes_at_2100_local() -> None:
    timezone = ZoneInfo('America/Santiago')

    before = MINE_CALENDAR.resolve_operational_day(
        datetime(2026, 8, 17, 20, 59, tzinfo=timezone)
    )
    boundary = MINE_CALENDAR.resolve_operational_day(
        datetime(2026, 8, 17, 21, 0, tzinfo=timezone)
    )

    assert before.operational_date.isoformat() == '2026-08-17'
    assert boundary.operational_date.isoformat() == '2026-08-18'
    assert boundary.start_local.isoformat() == '2026-08-17T21:00:00-04:00'


def test_plant_resolves_1900_0700_and_0700_1900_shifts() -> None:
    timezone = ZoneInfo('America/Santiago')

    night = PLANT_CALENDAR.resolve_work_shift(
        datetime(2026, 8, 17, 22, 0, tzinfo=timezone)
    )
    day = PLANT_CALENDAR.resolve_work_shift(
        datetime(2026, 8, 18, 10, 0, tzinfo=timezone)
    )

    assert night.code is WorkShiftCode.NIGHT
    assert night.turn == '001'
    assert night.start_local.isoformat() == '2026-08-17T19:00:00-04:00'
    assert night.end_local.isoformat() == '2026-08-18T07:00:00-04:00'
    assert day.code is WorkShiftCode.DAY
    assert day.turn == '002'
    assert day.start_local.isoformat() == '2026-08-18T07:00:00-04:00'
    assert day.end_local.isoformat() == '2026-08-18T19:00:00-04:00'


def test_mine_resolves_2100_0900_and_0900_2100_shifts() -> None:
    timezone = ZoneInfo('America/Santiago')

    night = MINE_CALENDAR.resolve_work_shift(
        datetime(2026, 8, 17, 23, 0, tzinfo=timezone)
    )
    day = MINE_CALENDAR.resolve_work_shift(
        datetime(2026, 8, 18, 12, 0, tzinfo=timezone)
    )

    assert night.code is WorkShiftCode.NIGHT
    assert night.turn == '001'
    assert night.start_local.isoformat() == '2026-08-17T21:00:00-04:00'
    assert night.end_local.isoformat() == '2026-08-18T09:00:00-04:00'
    assert day.code is WorkShiftCode.DAY
    assert day.turn == '002'
    assert day.start_local.isoformat() == '2026-08-18T09:00:00-04:00'
    assert day.end_local.isoformat() == '2026-08-18T21:00:00-04:00'


def test_plant_resolves_operational_week_from_friday() -> None:
    value = datetime(2026, 8, 6, 0, 0, tzinfo=UTC)

    week = PLANT_CALENDAR.resolve_operational_week(value)

    assert week.start_operational_date.isoformat() == '2026-07-31'
    assert week.end_operational_date.isoformat() == '2026-08-07'
    assert week.partition.as_mapping() == {
        'calendar': 'plant_19',
        'operational_year': '2026',
        'operational_week': 'W31',
    }


def test_week_exposes_fourteen_named_work_shifts() -> None:
    shifts = MINE_CALENDAR.build_work_shifts_for_week(datetime(2026, 8, 6, 2, 0, tzinfo=UTC))

    assert len(shifts) == 14
    assert [item.turn for item in shifts[:4]] == ['001', '002', '001', '002']
    assert shifts[0].operational_date.isoformat() == '2026-07-31'
    assert shifts[-1].operational_date.isoformat() == '2026-08-06'


def test_additional_calendar_is_explicit_and_not_hard_coded() -> None:
    calendar = OperationalCalendar(
        key='future_20',
        operational_day_start_hour=20,
        day_shift_start_hour=8,
        week_start_weekday=0,
    )

    result = calendar.resolve_operational_day(datetime(2026, 8, 6, 1, 0, tzinfo=UTC))

    assert result.calendar == 'future_20'
    assert result.start_local.hour == 20


def test_public_resolvers_reject_naive_datetimes() -> None:
    with pytest.raises(ValueError, match='timezone-aware'):
        PLANT_CALENDAR.resolve_operational_day(datetime(2026, 8, 5, 19, 0))
