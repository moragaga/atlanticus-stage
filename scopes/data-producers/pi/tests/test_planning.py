from datetime import UTC, datetime, timedelta, timezone

import pytest

from atlanticus.data_producers.pi import PiDataProducerPlannerError, PiSlotPlanner


def test_floor_slot_uses_closed_utc_slot_without_microseconds() -> None:
    planner = PiSlotPlanner(interpolation_seconds=10)

    assert planner.floor_slot(datetime(2026, 8, 14, 10, 9, 11, 999999, tzinfo=UTC)) == datetime(
        2026, 8, 14, 10, 9, 10, tzinfo=UTC
    )
    assert planner.floor_slot(datetime(2026, 8, 14, 10, 9, 24, tzinfo=UTC)) == datetime(
        2026, 8, 14, 10, 9, 20, tzinfo=UTC
    )


def test_floor_slot_supports_other_interpolation_without_new_time_axis() -> None:
    planner = PiSlotPlanner(
        interpolation_seconds=20,
        max_recovery_lookback_seconds=3600,
        max_recovery_window_seconds=3600,
    )

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


def test_plan_collects_all_missing_slots_when_they_fit_recovery_window() -> None:
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


def test_recovery_discards_history_older_than_configured_lookback() -> None:
    planner = PiSlotPlanner(
        interpolation_seconds=10,
        max_recovery_lookback_seconds=3600,
        max_recovery_window_seconds=3600,
    )

    window = planner.plan(
        now_utc=datetime(2026, 8, 14, 12, 0, 7, tzinfo=UTC),
        committed_watermark_utc=datetime(2026, 8, 14, 7, 0, 0, tzinfo=UTC),
    )

    assert window is not None
    assert window.first_slot_utc == datetime(2026, 8, 14, 11, 0, 10, tzinfo=UTC)
    assert window.last_slot_utc == datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)
    assert window.slot_count == 360
    assert window.recovery_truncated is True


def test_recovery_window_slices_only_the_allowed_lookback() -> None:
    planner = PiSlotPlanner(
        interpolation_seconds=10,
        max_recovery_lookback_seconds=3600,
        max_recovery_window_seconds=900,
    )
    now = datetime(2026, 8, 14, 12, 0, 7, tzinfo=UTC)

    first = planner.plan(
        now_utc=now,
        committed_watermark_utc=datetime(2026, 8, 14, 7, 0, 0, tzinfo=UTC),
    )
    assert first is not None
    assert first.first_slot_utc == datetime(2026, 8, 14, 11, 0, 10, tzinfo=UTC)
    assert first.last_slot_utc == datetime(2026, 8, 14, 11, 15, 0, tzinfo=UTC)
    assert first.slot_count == 90
    assert first.recovery_truncated is True

    second = planner.plan(
        now_utc=now,
        committed_watermark_utc=first.last_slot_utc,
    )
    assert second is not None
    assert second.first_slot_utc == datetime(2026, 8, 14, 11, 15, 10, tzinfo=UTC)
    assert second.last_slot_utc == datetime(2026, 8, 14, 11, 30, 0, tzinfo=UTC)
    assert second.slot_count == 90
    assert second.recovery_truncated is False


def test_next_wake_is_boundary_when_caught_up_and_immediate_with_backlog() -> None:
    planner = PiSlotPlanner(interpolation_seconds=10)

    now = datetime(2026, 8, 14, 12, 0, 1, 250000, tzinfo=UTC)
    assert planner.next_wake_at(
        now_utc=now,
        committed_watermark_utc=datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC),
    ) == datetime(2026, 8, 14, 12, 0, 10, tzinfo=UTC)

    backlog_now = datetime(2026, 8, 14, 12, 0, 21, 250000, tzinfo=UTC)
    assert (
        planner.next_wake_at(
            now_utc=backlog_now,
            committed_watermark_utc=datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC),
        )
        == backlog_now
    )
    assert planner.next_wake_at(now_utc=now, committed_watermark_utc=None) == now


def test_planner_rejects_non_utc_and_unaligned_committed_watermarks() -> None:
    planner = PiSlotPlanner(interpolation_seconds=10)

    with pytest.raises(PiDataProducerPlannerError, match='must use UTC'):
        planner.plan(
            now_utc=datetime(2026, 8, 14, 10, 0, tzinfo=timezone(timedelta(hours=-4))),
            committed_watermark_utc=None,
        )

    with pytest.raises(PiDataProducerPlannerError, match='must be aligned'):
        planner.plan(
            now_utc=datetime(2026, 8, 14, 10, 0, 20, tzinfo=UTC),
            committed_watermark_utc=datetime(2026, 8, 14, 10, 0, 11, tzinfo=UTC),
        )


def test_planner_requires_recovery_contract_to_align_with_interpolation() -> None:
    with pytest.raises(PiDataProducerPlannerError, match='must be divisible'):
        PiSlotPlanner(
            interpolation_seconds=20,
            max_recovery_lookback_seconds=3590,
            max_recovery_window_seconds=3580,
        )

    with pytest.raises(PiDataProducerPlannerError, match='must not exceed'):
        PiSlotPlanner(
            interpolation_seconds=10,
            max_recovery_lookback_seconds=900,
            max_recovery_window_seconds=1800,
        )
