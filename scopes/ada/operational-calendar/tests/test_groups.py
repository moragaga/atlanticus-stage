from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from ada.operational_calendar import (
    TurnArea,
    WorkShiftCode,
    build_turn_calendar_rows,
    get_current_turn,
    get_previous_turn,
    get_turns_for_incremental_window,
)


def test_group_calendar_uses_independent_0800_2000_boundaries() -> None:
    timezone = ZoneInfo('America/Santiago')

    day = get_current_turn(
        area=TurnArea.MINA,
        value=datetime(2026, 8, 17, 9, 0, tzinfo=timezone),
    )
    night = get_current_turn(
        area=TurnArea.MINA,
        value=datetime(2026, 8, 17, 21, 0, tzinfo=timezone),
    )

    assert day.turn_code is WorkShiftCode.DAY
    assert day.turn_start_local.isoformat() == '2026-08-17T08:00:00-04:00'
    assert day.turn_end_local.isoformat() == '2026-08-17T20:00:00-04:00'
    assert night.turn_code is WorkShiftCode.NIGHT
    assert night.turn_start_local.isoformat() == '2026-08-17T20:00:00-04:00'
    assert night.turn_end_local.isoformat() == '2026-08-18T08:00:00-04:00'


def test_validated_group_rotation_is_preserved() -> None:
    mine_day = get_current_turn(
        area=TurnArea.MINA,
        value=datetime(2022, 1, 5, 12, 0, tzinfo=UTC),
    )
    mine_night = get_current_turn(
        area=TurnArea.MINA,
        value=datetime(2022, 1, 5, 1, 0, tzinfo=UTC),
    )
    plant_day = get_current_turn(
        area=TurnArea.PLANTA,
        value=datetime(2022, 1, 6, 12, 0, tzinfo=UTC),
    )
    plant_night = get_current_turn(
        area=TurnArea.PLANTA,
        value=datetime(2022, 1, 6, 1, 0, tzinfo=UTC),
    )

    assert mine_day.group == 'G1'
    assert mine_night.group == 'G2'
    assert plant_day.group == 'G1'
    assert plant_night.group == 'G2'


def test_group_rotation_alternates_weekly() -> None:
    current = get_current_turn(
        area='mina',
        value=datetime(2022, 1, 5, 12, 0, tzinfo=UTC),
    )
    next_week = get_current_turn(
        area='mina',
        value=datetime(2022, 1, 12, 12, 0, tzinfo=UTC),
    )

    assert current.group == 'G1'
    assert next_week.group == 'G3'


def test_group_incremental_window_returns_previous_and_current() -> None:
    previous, current = get_turns_for_incremental_window(
        area='planta',
        value=datetime(2026, 8, 6, 1, 0, tzinfo=UTC),
    )

    assert previous == get_previous_turn(
        area='planta',
        value=datetime(2026, 8, 6, 1, 0, tzinfo=UTC),
    )
    assert previous.turn_end_utc == current.turn_start_utc


def test_group_calendar_rows_keep_two_shifts_per_date() -> None:
    rows = build_turn_calendar_rows(
        start_date=date(2026, 8, 5),
        end_date=date(2026, 8, 6),
        area='mina',
    )

    assert len(rows) == 4
    assert [item['turno'] for item in rows] == ['day', 'night', 'day', 'night']


def test_group_rejects_unknown_area() -> None:
    with pytest.raises(ValueError, match='area must be one of'):
        get_current_turn(area='unknown')
