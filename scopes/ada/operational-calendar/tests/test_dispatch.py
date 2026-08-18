from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ada.operational_calendar import (
    SHIFT_ID_TURN_WINDOW_SIZE,
    get_current_shift_id_turn,
    get_previous_shift_id_turn,
    get_shift_id_turn_window,
    parse_shift_id_turn,
)


def test_dispatch_default_window_is_current_and_previous_only() -> None:
    assert SHIFT_ID_TURN_WINDOW_SIZE == 2

    turns = get_shift_id_turn_window(value=datetime(2026, 8, 17, 22, 0, tzinfo=UTC))

    assert [item.shift_id for item in turns] == [260817002, 260817001]


def test_shift_id_uses_nominal_operational_date_and_turn_suffix() -> None:
    current = get_current_shift_id_turn(value=datetime(2026, 8, 6, 2, 0, tzinfo=UTC))
    previous = get_previous_shift_id_turn(value=datetime(2026, 8, 6, 2, 0, tzinfo=UTC))

    assert current.shift_id == 260806001
    assert current.partition == {
        'year': '2026',
        'month': '08',
        'day': '06',
        'turn': '001',
    }
    assert previous.shift_id == 260805002


def test_shift_id_parser_uses_mine_2100_0900_calendar() -> None:
    night = parse_shift_id_turn('260806001')
    day = parse_shift_id_turn('260805002')

    assert night.shift_start_local.isoformat() == '2026-08-05T21:00:00-04:00'
    assert night.shift_end_local.isoformat() == '2026-08-06T09:00:00-04:00'
    assert day.shift_start_local.isoformat() == '2026-08-05T09:00:00-04:00'
    assert day.shift_end_local.isoformat() == '2026-08-05T21:00:00-04:00'


def test_shift_window_can_expand_explicitly_without_changing_default() -> None:
    turns = get_shift_id_turn_window(
        value=datetime(2026, 8, 6, 2, 0, tzinfo=UTC),
        window_size=4,
    )

    assert [item.shift_id for item in turns] == [
        260806001,
        260805002,
        260805001,
        260804002,
    ]


@pytest.mark.parametrize('value', ['260805003', '26080501', 'shift'])
def test_shift_parser_rejects_invalid_identifiers(value: str) -> None:
    with pytest.raises(ValueError):
        parse_shift_id_turn(value)
