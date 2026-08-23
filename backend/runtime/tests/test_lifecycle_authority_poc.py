from __future__ import annotations

import json
import signal
from datetime import UTC, datetime, timedelta

import pytest

from atlanticus.runtime import (
    JobDefinition,
    JobRuntimeContext,
    LeaseOwnershipLostError,
    LeaseRenewalError,
    RuntimeConfiguration,
    RuntimeContractError,
    execute_job,
)
from atlanticus.runtime.lease import ExecutionLease


def _environment(tmp_path) -> dict[str, str]:
    return {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': str(tmp_path),
    }


def _definition(*, service_name: str = 'lifecycle-job') -> JobDefinition:
    return JobDefinition(
        module_name='lifecycle_job',
        service_name=service_name,
        run_once=True,
        iteration_timeout_seconds=5,
        execution_timeout_seconds=12,
        shutdown_grace_seconds=2,
        lease_timeout_seconds=8,
        lease_renew_seconds=1,
        lease_wait_seconds=0,
        resource_sample_seconds=0.01,
    )


class MutableUtcClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _lease(
    tmp_path,
    *,
    run_id: str,
    clock: MutableUtcClock,
    lease_timeout_seconds: float = 10,
) -> ExecutionLease:
    return ExecutionLease(
        volume_path=tmp_path,
        application='ada',
        service_name='authority-job',
        module_name='authority_job',
        run_id=run_id,
        lease_timeout_seconds=lease_timeout_seconds,
        renewal_seconds=max(0.1, lease_timeout_seconds / 3),
        wait_seconds=0,
        poll_seconds=0.01,
        wall_clock=clock,
    )


def test_poc_recovery_iteration_and_drain_share_one_lease_authority(tmp_path) -> None:
    order: list[str] = []
    generations: list[int | None] = []

    def recovery(context: JobRuntimeContext) -> None:
        context.assert_lease_current()
        generations.append(context.lease_generation)
        order.append('recovery')
        context.set_memory('recovered', True)

    def iteration(context: JobRuntimeContext) -> None:
        context.assert_lease_current()
        assert context.get_memory('recovered') is True
        generations.append(context.lease_generation)
        order.append('iteration')

    def drain(context: JobRuntimeContext) -> None:
        context.assert_lease_current()
        generations.append(context.lease_generation)
        order.append('drain')

    result = execute_job(
        definition=_definition(),
        recovery=recovery,
        iteration=iteration,
        drain=drain,
        argv=[],
        environ=_environment(tmp_path),
    )

    assert result.status.value == 'success'
    assert result.iteration_count == 1
    assert order == ['recovery', 'iteration', 'drain']
    assert generations == [1, 1, 1]


def test_poc_recovery_failure_prevents_business_and_drain(tmp_path) -> None:
    calls: list[str] = []

    def recovery(context: JobRuntimeContext) -> None:
        calls.append('recovery')
        raise ValueError('recovery failed')

    with pytest.raises(ValueError, match='recovery failed'):
        execute_job(
            definition=_definition(service_name='recovery-failure-job'),
            recovery=recovery,
            iteration=lambda context: calls.append('iteration'),
            drain=lambda context: calls.append('drain'),
            argv=[],
            environ=_environment(tmp_path),
        )

    assert calls == ['recovery']
    assert not (tmp_path / 'ada' / '.runtime' / 'leases' / 'recovery-failure-job.json').exists()


def test_poc_sigterm_drains_before_heartbeat_stops(tmp_path, monkeypatch) -> None:
    lifecycle: list[str] = []
    lease_holder: list[ExecutionLease] = []
    original_start = ExecutionLease.start_renewal
    original_stop = ExecutionLease.stop_renewal

    def tracking_start(self, *, on_lost=None) -> None:
        lease_holder.append(self)
        original_start(self, on_lost=on_lost)

    def tracking_stop(self) -> None:
        lifecycle.append('stop_renewal')
        original_stop(self)

    monkeypatch.setattr(ExecutionLease, 'start_renewal', tracking_start)
    monkeypatch.setattr(ExecutionLease, 'stop_renewal', tracking_stop)

    def iteration(context: JobRuntimeContext) -> None:
        lifecycle.append('iteration')
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)

    def drain(context: JobRuntimeContext) -> None:
        lifecycle.append('drain')
        assert lease_holder
        thread = lease_holder[0]._renewal_thread
        assert thread is not None and thread.is_alive()
        context.assert_lease_current()

    result = execute_job(
        definition=_definition(service_name='sigterm-drain-job'),
        iteration=iteration,
        drain=drain,
        argv=[],
        environ=_environment(tmp_path),
    )

    assert result.status.value == 'warning'
    assert result.stop_reason == 'sigterm'
    assert lifecycle[:2] == ['iteration', 'drain']
    assert lifecycle.index('drain') < lifecycle.index('stop_renewal')


def test_poc_stale_writer_cannot_commit_after_takeover(tmp_path) -> None:
    wall_clock = MutableUtcClock(datetime(2026, 8, 23, 21, 10, 0, tzinfo=UTC))
    first = _lease(tmp_path, run_id='run-1', clock=wall_clock)
    first_acquisition = first.acquire()
    configuration = RuntimeConfiguration.from_sources(environ=_environment(tmp_path))
    context = JobRuntimeContext.create(
        definition=_definition(service_name='authority-context-job'),
        configuration=configuration,
        run_id='11111111-1111-1111-1111-111111111111',
        correlation_id='22222222-2222-2222-2222-222222222222',
    )
    assert first_acquisition.generation is not None
    context._bind_lease_authority(
        generation=first_acquisition.generation,
        checker=first.assert_current,
    )

    wall_clock.value += timedelta(seconds=11)
    second = _lease(tmp_path, run_id='run-2', clock=wall_clock)
    second_acquisition = second.acquire()
    commits: list[str] = []

    def stale_commit() -> None:
        context.assert_lease_current()
        commits.append('committed')

    with pytest.raises(LeaseOwnershipLostError, match='ownership'):
        stale_commit()

    assert first_acquisition.generation == 1
    assert second_acquisition.generation == 2
    assert commits == []
    assert second.release()


def test_poc_uncertain_renewal_blocks_new_commit(tmp_path, monkeypatch) -> None:
    wall_clock = MutableUtcClock(datetime(2026, 8, 23, 21, 10, 0, tzinfo=UTC))
    lease = _lease(tmp_path, run_id='run-1', clock=wall_clock, lease_timeout_seconds=1)
    acquisition = lease.acquire()
    configuration = RuntimeConfiguration.from_sources(environ=_environment(tmp_path))
    context = JobRuntimeContext.create(
        definition=_definition(service_name='renewal-context-job'),
        configuration=configuration,
        run_id='11111111-1111-1111-1111-111111111111',
        correlation_id='22222222-2222-2222-2222-222222222222',
    )
    assert acquisition.generation is not None
    context._bind_lease_authority(
        generation=acquisition.generation,
        checker=lease.assert_current,
    )

    def fail_renew(self) -> bool:
        raise OSError('renewal transport unavailable')

    monkeypatch.setattr(ExecutionLease, 'renew', fail_renew)
    lease._renewal_seconds = 0.01
    lease.start_renewal(on_lost=context.request_stop)
    for _ in range(100):
        if lease.failure is not None:
            break
        context._stop.wait(0.01)

    commits: list[str] = []

    def commit() -> None:
        context.assert_lease_current()
        commits.append('committed')

    with pytest.raises(LeaseRenewalError, match='renewal failed'):
        commit()

    assert commits == []
    assert context.stop_reason == 'lease_renewal_failed'
    lease.release()


def test_poc_context_fails_closed_without_bound_authority(tmp_path) -> None:
    context = JobRuntimeContext.create(
        definition=_definition(service_name='unbound-authority-job'),
        configuration=RuntimeConfiguration.from_sources(environ=_environment(tmp_path)),
        run_id='11111111-1111-1111-1111-111111111111',
        correlation_id='22222222-2222-2222-2222-222222222222',
    )

    assert context.lease_generation is None
    with pytest.raises(RuntimeContractError, match='authority is not available'):
        context.assert_lease_current()


def test_poc_failed_drain_does_not_complete_scheduled_slot(tmp_path, monkeypatch) -> None:
    import atlanticus.runtime.context as context_module

    fixed_now = datetime(2026, 8, 23, 21, 10, 5, tzinfo=UTC)
    monkeypatch.setattr(context_module, '_utc_now', lambda: fixed_now)
    environment = {
        **_environment(tmp_path),
        'ATLANTICUS_JOB_SCHEDULE_CRON': '*/10 * * * *',
    }
    definition = JobDefinition(
        module_name='lifecycle_job',
        service_name='scheduled-drain-job',
        run_once=True,
        iteration_timeout_seconds=5,
        execution_timeout_seconds=600,
        shutdown_grace_seconds=20,
        lease_timeout_seconds=30,
        lease_renew_seconds=5,
        lease_wait_seconds=0,
        resource_sample_seconds=0.01,
    )
    calls: list[str] = []

    def fail_drain(context: JobRuntimeContext) -> None:
        context.assert_lease_current()
        raise ValueError('drain failed')

    with pytest.raises(ValueError, match='drain failed'):
        execute_job(
            definition=definition,
            iteration=lambda context: calls.append('first'),
            drain=fail_drain,
            argv=[],
            environ=environment,
        )

    result = execute_job(
        definition=definition,
        iteration=lambda context: calls.append('retry'),
        argv=[],
        environ=environment,
    )

    authority_path = tmp_path / 'ada' / '.runtime' / 'authority' / 'scheduled-drain-job.json'
    authority = json.loads(authority_path.read_text(encoding='utf-8'))
    assert result.status.value == 'success'
    assert calls == ['first', 'retry']
    assert authority['generation'] == 2
    assert authority['last_completed_scheduled_at_utc'] == '2026-08-23T21:10:00+00:00'


def test_execute_job_rejects_non_callable_lifecycle_hooks(tmp_path) -> None:
    definition = _definition(service_name='invalid-hook-job')

    with pytest.raises(TypeError, match='recovery must be callable'):
        execute_job(
            definition=definition,
            iteration=lambda context: None,
            recovery=object(),
            argv=[],
            environ=_environment(tmp_path),
        )
    with pytest.raises(TypeError, match='drain must be callable'):
        execute_job(
            definition=definition,
            iteration=lambda context: None,
            drain=object(),
            argv=[],
            environ=_environment(tmp_path),
        )
