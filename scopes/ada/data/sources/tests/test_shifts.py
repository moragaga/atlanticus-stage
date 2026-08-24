from datetime import UTC, datetime

from ada.data.core import ShiftScope, ShiftSelection
from ada.data.sources import MineShiftResolver


def _ids(selection: ShiftSelection, timestamp: datetime) -> tuple[int, ...]:
    return tuple(
        item.shift_id
        for item in MineShiftResolver().resolve(
            selection=selection,
            as_of=timestamp,
        )
    )


def test_current_and_previous_are_individual_mine_shift_ids() -> None:
    watermark = datetime(2026, 8, 20, 2, 0, tzinfo=UTC)

    assert _ids(ShiftSelection(ShiftScope.CURRENT), watermark) == (260820001,)
    assert _ids(ShiftSelection(ShiftScope.PREVIOUS), watermark) == (260819002,)


def test_current_turn_is_current_operational_day_without_future_shift() -> None:
    night_watermark = datetime(2026, 8, 20, 2, 0, tzinfo=UTC)
    day_watermark = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)

    assert _ids(ShiftSelection(ShiftScope.CURRENT_TURN), night_watermark) == (260820001,)
    assert _ids(ShiftSelection(ShiftScope.CURRENT_TURN), day_watermark) == (
        260820001,
        260820002,
    )


def test_previous_turn_is_full_previous_operational_day() -> None:
    watermark = datetime(2026, 8, 20, 2, 0, tzinfo=UTC)

    assert _ids(ShiftSelection(ShiftScope.PREVIOUS_TURN), watermark) == (
        260819001,
        260819002,
    )


def test_days_include_current_partial_day_and_prior_full_days() -> None:
    watermark = datetime(2026, 8, 20, 2, 0, tzinfo=UTC)

    assert _ids(ShiftSelection(ShiftScope.DAYS, days=2), watermark) == (
        260819001,
        260819002,
        260820001,
    )


def test_current_week_starts_on_mine_operational_friday_and_stops_at_current_shift() -> None:
    watermark = datetime(2026, 8, 23, 14, 0, tzinfo=UTC)

    assert _ids(ShiftSelection(ShiftScope.CURRENT_WEEK), watermark) == (
        260821001,
        260821002,
        260822001,
        260822002,
        260823001,
        260823002,
    )
