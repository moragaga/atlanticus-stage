from datetime import UTC, datetime

from ada.data.core import OperationalScope
from ada.data.sources import OperationalWindowResolver
from ada.operational_calendar import MINE_CALENDAR, PLANT_CALENDAR


def _as_of() -> datetime:
    return datetime(2026, 8, 20, 2, 0, tzinfo=UTC)


def test_current_turn_uses_requested_mine_or_plant_calendar() -> None:
    resolver = OperationalWindowResolver()
    as_of = _as_of()

    mine = resolver.resolve(scope=OperationalScope.CURRENT_TURN_MINE, as_of=as_of)
    plant = resolver.resolve(scope=OperationalScope.CURRENT_TURN_PLANT, as_of=as_of)

    expected_mine = MINE_CALENDAR.resolve_work_shift(as_of)
    expected_plant = PLANT_CALENDAR.resolve_work_shift(as_of)
    assert mine.start_utc == expected_mine.start_utc
    assert mine.end_utc == as_of
    assert plant.start_utc == expected_plant.start_utc
    assert plant.end_utc == as_of


def test_previous_turn_uses_complete_previous_shift() -> None:
    resolver = OperationalWindowResolver()
    as_of = _as_of()
    window = resolver.resolve(
        scope=OperationalScope.PREVIOUS_TURN_MINE,
        as_of=as_of,
    )
    expected = MINE_CALENDAR.resolve_previous_work_shift(as_of)

    assert (window.start_utc, window.end_utc) == (expected.start_utc, expected.end_utc)


def test_operational_day_uses_area_specific_boundary() -> None:
    resolver = OperationalWindowResolver()
    as_of = _as_of()
    mine = resolver.resolve(
        scope=OperationalScope.CURRENT_OPERATIONAL_DAY_MINE,
        as_of=as_of,
    )
    plant = resolver.resolve(
        scope=OperationalScope.CURRENT_OPERATIONAL_DAY_PLANT,
        as_of=as_of,
    )

    assert mine.start_utc == MINE_CALENDAR.resolve_operational_day(as_of).start_utc
    assert plant.start_utc == PLANT_CALENDAR.resolve_operational_day(as_of).start_utc


def test_operational_month_starts_day_before_first_operational_date_at_area_boundary() -> None:
    resolver = OperationalWindowResolver()
    as_of = datetime(2026, 8, 15, 12, tzinfo=UTC)

    mine = resolver.resolve(
        scope=OperationalScope.CURRENT_OPERATIONAL_MONTH_MINE,
        as_of=as_of,
    )
    plant = resolver.resolve(
        scope=OperationalScope.CURRENT_OPERATIONAL_MONTH_PLANT,
        as_of=as_of,
    )

    first_mine_day = MINE_CALENDAR.resolve_operational_day(mine.start_utc).operational_date
    first_plant_day = PLANT_CALENDAR.resolve_operational_day(plant.start_utc).operational_date
    assert first_mine_day.isoformat() == '2026-08-01'
    assert first_plant_day.isoformat() == '2026-08-01'
    assert mine.end_utc == as_of
    assert plant.end_utc == as_of
