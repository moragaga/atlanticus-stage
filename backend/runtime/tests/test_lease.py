from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

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
    with pytest.raises(ConcurrentExecutionError):
        _lease(tmp_path, run_id='run-2').acquire()

    assert first.release()
    second = _lease(tmp_path, run_id='run-2')
    second.acquire()
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
