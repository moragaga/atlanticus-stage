from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from atlanticus.runtime import AtlanticusRuntimeError
from atlanticus.runtime.lease import ExecutionLease


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _lease(
    tmp_path,
    *,
    run_id: str,
    clock: MutableClock,
    scheduled_at_utc: datetime | None = None,
    authority_deadline_utc: datetime | None = None,
    lease_timeout_seconds: float = 120,
) -> ExecutionLease:
    return ExecutionLease(
        volume_path=tmp_path,
        application='ada',
        service_name='authority-job',
        module_name='authority_job',
        run_id=run_id,
        lease_timeout_seconds=lease_timeout_seconds,
        renewal_seconds=min(10, lease_timeout_seconds / 2),
        wait_seconds=0,
        poll_seconds=0.01,
        instance_id=f'instance-{run_id}',
        process_id=101,
        scheduled_at_utc=scheduled_at_utc,
        authority_deadline_utc=authority_deadline_utc,
        wall_clock=clock,
    )


def test_poc_generation_is_durable_across_clean_release(tmp_path) -> None:
    clock = MutableClock(datetime(2026, 8, 23, 21, 10, 5, tzinfo=UTC))
    first = _lease(tmp_path, run_id='run-1', clock=clock)

    first_acquisition = first.acquire()
    assert first_acquisition.generation == 1
    assert first.release()

    second = _lease(tmp_path, run_id='run-2', clock=clock)
    second_acquisition = second.acquire()

    assert second_acquisition.generation == 2
    authority = json.loads(second.authority_path.read_text(encoding='utf-8'))
    assert authority['generation'] == 2
    assert second.release()


def test_poc_completed_scheduled_slot_is_not_executed_twice(tmp_path) -> None:
    scheduled_at = datetime(2026, 8, 23, 21, 10, 0, tzinfo=UTC)
    deadline = datetime(2026, 8, 23, 21, 20, 0, tzinfo=UTC)
    clock = MutableClock(datetime(2026, 8, 23, 21, 10, 5, tzinfo=UTC))
    first = _lease(
        tmp_path,
        run_id='run-1',
        clock=clock,
        scheduled_at_utc=scheduled_at,
        authority_deadline_utc=deadline,
    )

    first_acquisition = first.acquire()
    assert first_acquisition.generation == 1
    assert first.release(completed=True)

    duplicate = _lease(
        tmp_path,
        run_id='run-2',
        clock=clock,
        scheduled_at_utc=scheduled_at,
        authority_deadline_utc=deadline,
    )
    duplicate_acquisition = duplicate.acquire()

    assert duplicate_acquisition.skipped_reason == 'scheduled_slot_completed'
    assert duplicate_acquisition.generation == 1
    assert duplicate.acquired is False
    assert duplicate.path.exists() is False


def test_poc_incomplete_scheduled_slot_can_be_retried_with_new_generation(tmp_path) -> None:
    scheduled_at = datetime(2026, 8, 23, 21, 10, 0, tzinfo=UTC)
    deadline = datetime(2026, 8, 23, 21, 20, 0, tzinfo=UTC)
    clock = MutableClock(datetime(2026, 8, 23, 21, 10, 5, tzinfo=UTC))
    first = _lease(
        tmp_path,
        run_id='run-1',
        clock=clock,
        scheduled_at_utc=scheduled_at,
        authority_deadline_utc=deadline,
    )

    assert first.acquire().generation == 1
    assert first.release(completed=False)

    retry = _lease(
        tmp_path,
        run_id='run-2',
        clock=clock,
        scheduled_at_utc=scheduled_at,
        authority_deadline_utc=deadline,
    )
    retry_acquisition = retry.acquire()

    assert retry_acquisition.generation == 2
    assert retry_acquisition.skipped_reason is None
    assert retry.release(completed=True)


def test_poc_expired_takeover_advances_generation(tmp_path) -> None:
    clock = MutableClock(datetime(2026, 8, 23, 21, 10, 0, tzinfo=UTC))
    first = _lease(
        tmp_path,
        run_id='run-1',
        clock=clock,
        lease_timeout_seconds=10,
    )
    assert first.acquire().generation == 1

    clock.value += timedelta(seconds=11)
    second = _lease(
        tmp_path,
        run_id='run-2',
        clock=clock,
        lease_timeout_seconds=10,
    )
    acquisition = second.acquire()

    assert acquisition.generation == 2
    assert acquisition.recovered is not None
    assert acquisition.recovered.run_id == 'run-1'
    assert second.release()


def test_poc_renewal_never_crosses_authority_deadline(tmp_path) -> None:
    started_at = datetime(2026, 8, 23, 21, 10, 0, tzinfo=UTC)
    deadline = started_at + timedelta(seconds=30)
    clock = MutableClock(started_at)
    lease = _lease(
        tmp_path,
        run_id='run-1',
        clock=clock,
        authority_deadline_utc=deadline,
        lease_timeout_seconds=120,
    )

    lease.acquire()
    payload = json.loads(lease.path.read_text(encoding='utf-8'))
    assert datetime.fromisoformat(payload['expires_at_utc']) == deadline

    clock.value += timedelta(seconds=20)
    assert lease.renew() is True
    payload = json.loads(lease.path.read_text(encoding='utf-8'))
    assert datetime.fromisoformat(payload['expires_at_utc']) == deadline

    clock.value = deadline
    assert lease.renew() is False
    assert lease.acquired is False


def test_poc_elapsed_authority_window_never_creates_a_lease(tmp_path) -> None:
    deadline = datetime(2026, 8, 23, 21, 20, 0, tzinfo=UTC)
    clock = MutableClock(deadline)
    lease = _lease(
        tmp_path,
        run_id='run-1',
        clock=clock,
        scheduled_at_utc=datetime(2026, 8, 23, 21, 10, 0, tzinfo=UTC),
        authority_deadline_utc=deadline,
    )

    acquisition = lease.acquire()

    assert acquisition.skipped_reason == 'authority_window_elapsed'
    assert lease.acquired is False
    assert lease.path.exists() is False
    assert lease.authority_path.exists() is False


def test_poc_expired_owner_cannot_renew_same_generation(tmp_path) -> None:
    clock = MutableClock(datetime(2026, 8, 23, 21, 10, 0, tzinfo=UTC))
    lease = _lease(
        tmp_path,
        run_id='run-1',
        clock=clock,
        lease_timeout_seconds=10,
    )
    assert lease.acquire().generation == 1

    clock.value += timedelta(seconds=11)

    assert lease.renew() is False
    assert lease.acquired is False


def test_poc_renewal_waits_for_short_coordination_guard(tmp_path) -> None:
    clock = MutableClock(datetime(2026, 8, 23, 21, 10, 0, tzinfo=UTC))
    lease = _lease(
        tmp_path,
        run_id='run-1',
        clock=clock,
        lease_timeout_seconds=10,
    )
    assert lease.acquire().generation == 1

    guard_path = lease.path.parent / '.authority-job.recovery'
    descriptor = os.open(guard_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

    def release_guard() -> None:
        time.sleep(0.01)
        os.close(descriptor)
        guard_path.unlink(missing_ok=True)

    thread = threading.Thread(target=release_guard)
    thread.start()
    try:
        assert lease.renew() is True
    finally:
        thread.join()
    assert lease.release()


def test_poc_corrupt_authority_state_fails_closed(tmp_path) -> None:
    clock = MutableClock(datetime(2026, 8, 23, 21, 10, 0, tzinfo=UTC))
    lease = _lease(tmp_path, run_id='run-1', clock=clock)
    lease.authority_path.parent.mkdir(parents=True, exist_ok=True)
    lease.authority_path.write_text('{not-json', encoding='utf-8')

    with pytest.raises(AtlanticusRuntimeError, match='authority state is invalid'):
        lease.acquire()
