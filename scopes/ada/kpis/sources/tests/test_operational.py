from datetime import UTC, datetime

from ada.kpis.core import KpiOperationalScope, KpiWatermark
from ada.kpis.sources import KpiOperationalWindowResolver
from ada.operational_calendar import MINE_CALENDAR, PLANT_CALENDAR


def _watermark() -> KpiWatermark:
    return KpiWatermark(datetime(2026, 8, 20, 2, 0, tzinfo=UTC))


def test_current_turn_uses_requested_mine_or_plant_calendar() -> None:
    resolver = KpiOperationalWindowResolver()
    watermark = _watermark()

    mine = resolver.resolve(scope=KpiOperationalScope.CURRENT_TURN_MINE, watermark=watermark)
    plant = resolver.resolve(scope=KpiOperationalScope.CURRENT_TURN_PLANT, watermark=watermark)

    expected_mine = MINE_CALENDAR.resolve_work_shift(watermark.timestamp_utc)
    expected_plant = PLANT_CALENDAR.resolve_work_shift(watermark.timestamp_utc)
    assert mine.start_utc == expected_mine.start_utc
    assert mine.end_utc == watermark.timestamp_utc
    assert plant.start_utc == expected_plant.start_utc
    assert plant.end_utc == watermark.timestamp_utc


def test_previous_turn_uses_complete_previous_shift() -> None:
    resolver = KpiOperationalWindowResolver()
    watermark = _watermark()
    window = resolver.resolve(
        scope=KpiOperationalScope.PREVIOUS_TURN_MINE,
        watermark=watermark,
    )
    expected = MINE_CALENDAR.resolve_previous_work_shift(watermark.timestamp_utc)

    assert (window.start_utc, window.end_utc) == (expected.start_utc, expected.end_utc)


def test_operational_day_uses_area_specific_boundary() -> None:
    resolver = KpiOperationalWindowResolver()
    watermark = _watermark()
    mine = resolver.resolve(
        scope=KpiOperationalScope.CURRENT_OPERATIONAL_DAY_MINE,
        watermark=watermark,
    )
    plant = resolver.resolve(
        scope=KpiOperationalScope.CURRENT_OPERATIONAL_DAY_PLANT,
        watermark=watermark,
    )

    assert (
        mine.start_utc == MINE_CALENDAR.resolve_operational_day(watermark.timestamp_utc).start_utc
    )
    assert (
        plant.start_utc == PLANT_CALENDAR.resolve_operational_day(watermark.timestamp_utc).start_utc
    )


def test_operational_month_starts_day_before_first_operational_date_at_area_boundary() -> None:
    resolver = KpiOperationalWindowResolver()
    watermark = KpiWatermark(datetime(2026, 8, 15, 12, tzinfo=UTC))

    mine = resolver.resolve(
        scope=KpiOperationalScope.CURRENT_OPERATIONAL_MONTH_MINE,
        watermark=watermark,
    )
    plant = resolver.resolve(
        scope=KpiOperationalScope.CURRENT_OPERATIONAL_MONTH_PLANT,
        watermark=watermark,
    )

    first_mine_day = MINE_CALENDAR.resolve_operational_day(mine.start_utc).operational_date
    first_plant_day = PLANT_CALENDAR.resolve_operational_day(plant.start_utc).operational_date
    assert first_mine_day.isoformat() == '2026-08-01'
    assert first_plant_day.isoformat() == '2026-08-01'
    assert mine.end_utc == watermark.timestamp_utc
    assert plant.end_utc == watermark.timestamp_utc
