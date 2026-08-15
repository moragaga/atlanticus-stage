from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from threading import Event, Thread

import pytest

import atlanticus.runtime.lease as lease_module
from atlanticus.runtime import ConcurrentExecutionError, LeaseRenewalError
from atlanticus.runtime.lease import ExecutionLease


def _lease(tmp_path, *, run_id: str, wait_seconds: float = 0) -> ExecutionLease:
    return ExecutionLease(
        volume_path=tmp_path,
        application='ada',
        service_name='dispatch-ingestion-job',
        module_name='dispatch_ingestion',
        run_id=run_id,
        lease_timeout_seconds=350,
        wait_seconds=wait_seconds,
        poll_seconds=0.01,
        instance_id=f'instance-{run_id}',
        process_id=101,
    )


def test_lease_uses_application_runtime_scope_and_prevents_overlap(tmp_path) -> None:
    first = _lease(tmp_path, run_id='run-1')
    first.acquire()

    assert first.path == (tmp_path / 'ada' / '.runtime' / 'leases' / 'dispatch-ingestion-job.json')
    with pytest.raises(ConcurrentExecutionError, match='after waiting'):
        _lease(tmp_path, run_id='run-2').acquire()

    assert first.release()
    second = _lease(tmp_path, run_id='run-2')
    second.acquire()
    assert second.release()


def test_waiting_lease_acquires_after_clean_release(tmp_path) -> None:
    first = ExecutionLease(
        volume_path=tmp_path,
        application='ada',
        service_name='dispatch',
        job_key='dispatch-materialization',
        module_name='ada.processes.dispatch',
        run_id='run-1',
        lease_timeout_seconds=1,
        renewal_seconds=0.2,
        wait_seconds=0,
        poll_seconds=0.01,
    )
    second = ExecutionLease(
        volume_path=tmp_path,
        application='ada',
        service_name='dispatch',
        job_key='dispatch-materialization',
        module_name='ada.processes.dispatch',
        run_id='run-2',
        lease_timeout_seconds=1,
        renewal_seconds=0.2,
        wait_seconds=0.5,
        poll_seconds=0.01,
    )
    acquired = Event()
    errors: list[BaseException] = []

    def acquire_second() -> None:
        try:
            second.acquire()
            acquired.set()
        except BaseException as error:
            errors.append(error)

    first.acquire()
    thread = Thread(target=acquire_second)
    thread.start()
    time.sleep(0.05)

    assert not acquired.is_set()
    assert first.release()
    assert acquired.wait(timeout=0.5)
    thread.join(timeout=0.5)

    assert errors == []
    assert second.acquired is True
    assert second.release()


def test_waiting_lease_recovers_owner_that_expires_during_wait(tmp_path) -> None:
    first = ExecutionLease(
        volume_path=tmp_path,
        application='ada',
        service_name='dispatch',
        job_key='dispatch-materialization',
        module_name='ada.processes.dispatch',
        run_id='run-1',
        lease_timeout_seconds=0.08,
        renewal_seconds=0.02,
        wait_seconds=0,
        poll_seconds=0.01,
    )
    second = ExecutionLease(
        volume_path=tmp_path,
        application='ada',
        service_name='dispatch',
        job_key='dispatch-materialization',
        module_name='ada.processes.dispatch',
        run_id='run-2',
        lease_timeout_seconds=0.08,
        renewal_seconds=0.02,
        wait_seconds=0.5,
        poll_seconds=0.01,
    )
    first.acquire()

    acquisition = second.acquire()

    assert acquisition.waited_seconds >= 0.05
    assert acquisition.recovered is not None
    assert acquisition.recovered.run_id == 'run-1'
    assert second.acquired is True
    assert second.release()


def test_lease_recovers_expired_owner_without_creating_history(tmp_path) -> None:
    first = _lease(tmp_path, run_id='run-1')
    first.acquire()
    payload = json.loads(first.path.read_text(encoding='utf-8'))
    payload['expires_at_utc'] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    first.path.write_text(json.dumps(payload), encoding='utf-8')

    second = _lease(tmp_path, run_id='run-2')
    acquisition = second.acquire()

    assert acquisition.recovered is not None
    assert acquisition.recovered.run_id == 'run-1'
    assert json.loads(second.path.read_text(encoding='utf-8'))['run_id'] == 'run-2'
    assert list(second.path.parent.glob('*')) == [second.path]
    assert second.release()


def test_lease_can_use_job_key_independent_from_service_name(tmp_path) -> None:
    lease = ExecutionLease(
        volume_path=tmp_path,
        application='ada',
        service_name='dispatch',
        job_key='dispatch-materialization',
        module_name='ada.processes.dispatch',
        run_id='run-1',
        lease_timeout_seconds=120,
        renewal_seconds=30,
        wait_seconds=0,
    )

    lease.acquire()

    assert lease.path == tmp_path / 'ada/.runtime/leases/dispatch-materialization.json'
    assert lease.release()


def test_lease_renewal_extends_expiration_without_changing_owner(tmp_path) -> None:
    lease = ExecutionLease(
        volume_path=tmp_path,
        application='ada',
        service_name='dispatch',
        job_key='dispatch-materialization',
        module_name='ada.processes.dispatch',
        run_id='run-1',
        lease_timeout_seconds=120,
        renewal_seconds=30,
        wait_seconds=0,
    )
    lease.acquire()
    before = json.loads(lease.path.read_text(encoding='utf-8'))

    assert lease.renew() is True

    after = json.loads(lease.path.read_text(encoding='utf-8'))
    assert after['owner_token'] == before['owner_token']
    assert after['acquired_at_utc'] == before['acquired_at_utc']
    assert datetime.fromisoformat(after['expires_at_utc']) >= datetime.fromisoformat(
        before['expires_at_utc']
    )
    assert lease.release()


@pytest.mark.parametrize('job_key', ['job/key', 'job key', ' job', 'job '])
def test_lease_rejects_job_keys_instead_of_sanitizing_them(tmp_path, job_key) -> None:
    with pytest.raises(ValueError):
        ExecutionLease(
            volume_path=tmp_path,
            application='ada',
            service_name='dispatch',
            job_key=job_key,
            module_name='ada.processes.dispatch',
            run_id='run-1',
            lease_timeout_seconds=120,
            renewal_seconds=30,
            wait_seconds=0,
        )


def test_heartbeat_failure_requests_stop_and_is_observable(tmp_path, monkeypatch) -> None:
    lease = ExecutionLease(
        volume_path=tmp_path,
        application='ada',
        service_name='dispatch',
        job_key='dispatch-materialization',
        module_name='ada.processes.dispatch',
        run_id='run-1',
        lease_timeout_seconds=1,
        renewal_seconds=0.01,
        wait_seconds=0,
    )
    lease.acquire()
    stopped = Event()

    def fail(payload) -> None:
        raise OSError('unsafe storage detail')

    monkeypatch.setattr(lease, '_replace_payload', fail)
    lease.start_renewal(on_lost=lambda reason: stopped.set())

    assert stopped.wait(timeout=1)
    assert isinstance(lease.failure, LeaseRenewalError)
    with pytest.raises(LeaseRenewalError, match='Lease renewal failed'):
        lease.raise_if_unhealthy()


def test_initial_lease_creation_cannot_be_recovered_while_payload_is_still_being_written(
    tmp_path,
    monkeypatch,
) -> None:
    first = _lease(tmp_path, run_id='run-1')
    second = _lease(tmp_path, run_id='run-2')
    write_started = Event()
    allow_write = Event()
    first_error: list[BaseException] = []
    second_error: list[BaseException] = []
    original_write = lease_module._write_descriptor

    def blocking_write(descriptor: int, content: bytes) -> None:
        if b'"run_id": "run-1"' in content:
            write_started.set()
            assert allow_write.wait(timeout=1)
        original_write(descriptor, content)

    def acquire_first() -> None:
        try:
            first.acquire()
        except BaseException as error:
            first_error.append(error)

    def acquire_second() -> None:
        try:
            second.acquire()
        except BaseException as error:
            second_error.append(error)

    monkeypatch.setattr(lease_module, '_write_descriptor', blocking_write)
    first_thread = Thread(target=acquire_first)
    second_thread = Thread(target=acquire_second)
    first_thread.start()
    assert write_started.wait(timeout=1)
    second_thread.start()

    assert not second.acquired
    allow_write.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert first_error == []
    assert len(second_error) == 1
    assert isinstance(second_error[0], ConcurrentExecutionError)
    assert first.acquired is True
    assert second.acquired is False
    assert json.loads(first.path.read_text(encoding='utf-8'))['run_id'] == 'run-1'
    assert first.release()


def test_lease_fsyncs_directory_after_create_renew_and_release(tmp_path, monkeypatch) -> None:
    lease = _lease(tmp_path, run_id='run-1')
    calls = []

    monkeypatch.setattr(lease_module, '_fsync_directory', lambda directory: calls.append(directory))

    lease.acquire()
    lease.renew()
    assert lease.release()

    assert calls == [lease.path.parent, lease.path.parent, lease.path.parent]


def test_lease_recovers_invalid_utf8_payload_as_corrupt_state(tmp_path) -> None:
    lease = _lease(tmp_path, run_id='run-2')
    lease.path.parent.mkdir(parents=True, exist_ok=True)
    lease.path.write_bytes(b'\xff\xfe\x00')

    acquisition = lease.acquire()

    assert acquisition.recovered is not None
    assert acquisition.recovered.run_id is None
    assert json.loads(lease.path.read_text(encoding='utf-8'))['run_id'] == 'run-2'
    assert lease.release()


def test_lease_rejects_relative_volume_path() -> None:
    with pytest.raises(ValueError, match='absolute'):
        ExecutionLease(
            volume_path='relative-volume',
            application='ada',
            service_name='dispatch',
            module_name='ada.processes.dispatch',
            run_id='run-1',
            lease_timeout_seconds=120,
            renewal_seconds=30,
            wait_seconds=0,
        )


def test_lease_context_manager_does_not_hide_business_error(tmp_path, monkeypatch) -> None:
    lease = _lease(tmp_path, run_id='run-1')

    def fail_release() -> bool:
        raise OSError('cleanup failed')

    monkeypatch.setattr(lease, 'start_renewal', lambda: None)
    monkeypatch.setattr(lease, 'release', fail_release)

    with pytest.raises(ValueError, match='business failure'):
        with lease:
            raise ValueError('business failure')

    lease.path.unlink(missing_ok=True)


def test_stale_recovery_guard_without_lease_does_not_block_zero_wait_acquisition(tmp_path) -> None:
    lease = _lease(tmp_path, run_id='run-1', wait_seconds=0)
    lease.path.parent.mkdir(parents=True, exist_ok=True)
    guard = lease.path.parent / f'.{lease.path.stem}.recovery'
    guard.write_text('', encoding='utf-8')
    stale_at = time.time() - 10
    os.utime(guard, (stale_at, stale_at))

    acquisition = lease.acquire()

    assert acquisition.waited_seconds >= 0
    assert lease.acquired is True
    assert lease.release()
