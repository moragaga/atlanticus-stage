from datetime import UTC, datetime, timedelta, timezone

import pytest

from ada.processes.pi_web_api import PiSlotPlanner, PiWebApiPlannerError


def test_floor_slot_uses_closed_utc_slot_without_microseconds() -> None:
    planner = PiSlotPlanner(interpolation_seconds=10)

    assert planner.floor_slot(datetime(2026, 8, 14, 10, 9, 11, 999999, tzinfo=UTC)) == datetime(
        2026, 8, 14, 10, 9, 10, tzinfo=UTC
    )
    assert planner.floor_slot(datetime(2026, 8, 14, 10, 9, 24, tzinfo=UTC)) == datetime(
        2026, 8, 14, 10, 9, 20, tzinfo=UTC
    )


def test_floor_slot_supports_other_interpolation_without_new_time_axis() -> None:
    planner = PiSlotPlanner(interpolation_seconds=20, max_recovery_seconds=3600)

    assert planner.floor_slot(datetime(2026, 8, 14, 10, 10, 39, tzinfo=UTC)) == datetime(
        2026, 8, 14, 10, 10, 20, tzinfo=UTC
    )


def test_plan_returns_none_until_next_slot_exists() -> None:
    planner = PiSlotPlanner(interpolation_seconds=10)
    committed = datetime(2026, 8, 14, 10, 9, 10, tzinfo=UTC)

    assert (
        planner.plan(
            now_utc=datetime(2026, 8, 14, 10, 9, 11, tzinfo=UTC),
            committed_watermark_utc=committed,
        )
        is None
    )


def test_plan_collects_all_missing_slots_in_one_window() -> None:
    planner = PiSlotPlanner(interpolation_seconds=10)

    window = planner.plan(
        now_utc=datetime(2026, 8, 14, 10, 9, 53, tzinfo=UTC),
        committed_watermark_utc=datetime(2026, 8, 14, 10, 9, 20, tzinfo=UTC),
    )

    assert window is not None
    assert window.first_slot_utc == datetime(2026, 8, 14, 10, 9, 30, tzinfo=UTC)
    assert window.last_slot_utc == datetime(2026, 8, 14, 10, 9, 50, tzinfo=UTC)
    assert window.slot_count == 3
    assert window.recovery_truncated is False


def test_first_run_starts_at_current_closed_slot_only() -> None:
    planner = PiSlotPlanner(interpolation_seconds=10)

    window = planner.plan(
        now_utc=datetime(2026, 8, 14, 10, 9, 24, 750000, tzinfo=UTC),
        committed_watermark_utc=None,
    )

    assert window is not None
    assert window.first_slot_utc == datetime(2026, 8, 14, 10, 9, 20, tzinfo=UTC)
    assert window.last_slot_utc == window.first_slot_utc
    assert window.slot_count == 1


def test_recovery_is_capped_to_one_hour_of_slots_from_current_target() -> None:
    planner = PiSlotPlanner(interpolation_seconds=10, max_recovery_seconds=3600)

    window = planner.plan(
        now_utc=datetime(2026, 8, 14, 10, 0, 27, tzinfo=UTC),
        committed_watermark_utc=datetime(2026, 8, 14, 6, 0, 0, tzinfo=UTC),
    )

    assert window is not None
    assert window.last_slot_utc == datetime(2026, 8, 14, 10, 0, 20, tzinfo=UTC)
    assert window.first_slot_utc == datetime(2026, 8, 14, 9, 0, 30, tzinfo=UTC)
    assert window.slot_count == 360
    assert window.recovery_truncated is True


def test_planner_rejects_non_utc_and_unaligned_committed_watermarks() -> None:
    planner = PiSlotPlanner(interpolation_seconds=10)

    with pytest.raises(PiWebApiPlannerError, match='must use UTC'):
        planner.plan(
            now_utc=datetime(2026, 8, 14, 10, 0, tzinfo=timezone(timedelta(hours=-4))),
            committed_watermark_utc=None,
        )

    with pytest.raises(PiWebApiPlannerError, match='must be aligned'):
        planner.plan(
            now_utc=datetime(2026, 8, 14, 10, 0, 20, tzinfo=UTC),
            committed_watermark_utc=datetime(2026, 8, 14, 10, 0, 11, tzinfo=UTC),
        )


def test_planner_requires_recovery_to_be_exact_multiple_of_interpolation() -> None:
    with pytest.raises(PiWebApiPlannerError, match='must be divisible'):
        PiSlotPlanner(interpolation_seconds=20, max_recovery_seconds=3590)
