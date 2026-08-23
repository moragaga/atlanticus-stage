from __future__ import annotations

import json
import os
import signal
from datetime import UTC, datetime, timedelta

import pytest

from atlanticus.runtime import (
    JobDefinition,
    JobRuntimeContext,
    LeaseOwnershipLostError,
    RuntimeCancellationRequested,
    RuntimeConfiguration,
    execute_job,
)
from atlanticus.runtime._resource_sampler import CgroupResourceSampler
from atlanticus.runtime.lease import ExecutionLease, LeaseAcquisition
from atlanticus.runtime.runner import (
    _effective_lease_wait_seconds,
    _project_azure_observability_environ,
)


def _definition() -> JobDefinition:
    return JobDefinition(
        module_name='dispatch_ingestion',
        service_name='dispatch-ingestion-job',
        run_once=True,
        iteration_timeout_seconds=5,
        execution_timeout_seconds=10,
        shutdown_grace_seconds=2,
        lease_timeout_seconds=12,
        lease_wait_seconds=0,
        resource_sample_seconds=0.01,
    )


def _environment(tmp_path, environment='local') -> dict[str, str]:
    return {
        'ENVIRONMENT': environment,
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': str(tmp_path),
    }


def _day_directory(tmp_path):
    day = datetime.now(UTC).date().isoformat()
    return tmp_path / 'ada' / 'logs' / 'dispatch-ingestion-job' / f'day={day}'


def test_adaptive_lease_wait_uses_safe_remaining_budget(tmp_path) -> None:
    definition = JobDefinition(
        module_name='job',
        service_name='job-service',
        execution_timeout_seconds=60,
        shutdown_grace_seconds=10,
        iteration_timeout_seconds=20,
        lease_wait_seconds=None,
    )
    configuration = RuntimeConfiguration.from_sources(environ=_environment(tmp_path))
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='11111111-1111-1111-1111-111111111111',
        correlation_id='22222222-2222-2222-2222-222222222222',
        clock=lambda: 100.0,
    )

    assert _effective_lease_wait_seconds(definition, context) == 30


def test_explicit_lease_wait_is_capped_by_safe_remaining_budget(tmp_path) -> None:
    definition = JobDefinition(
        module_name='job',
        service_name='job-service',
        execution_timeout_seconds=60,
        shutdown_grace_seconds=10,
        iteration_timeout_seconds=20,
        lease_wait_seconds=90,
    )
    configuration = RuntimeConfiguration.from_sources(environ=_environment(tmp_path))
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='11111111-1111-1111-1111-111111111111',
        correlation_id='22222222-2222-2222-2222-222222222222',
        clock=lambda: 100.0,
    )

    assert _effective_lease_wait_seconds(definition, context) == 30


def test_lease_wait_consumes_execution_budget(tmp_path, monkeypatch) -> None:
    import atlanticus.runtime.runner as runner_module

    now = [100.0]
    captured_wait: list[float] = []
    observed_remaining: list[float] = []

    def fake_monotonic() -> float:
        return now[0]

    def fake_acquire(self) -> LeaseAcquisition:
        captured_wait.append(self._wait_seconds)
        now[0] += 20
        self._acquired = True
        acquisition = LeaseAcquisition(waited_seconds=20)
        self._acquisition = acquisition
        return acquisition

    monkeypatch.setattr(runner_module.time, 'monotonic', fake_monotonic)
    monkeypatch.setattr(ExecutionLease, 'acquire', fake_acquire)
    monkeypatch.setattr(ExecutionLease, 'start_renewal', lambda self, on_lost=None: None)
    monkeypatch.setattr(ExecutionLease, 'release', lambda self, completed=False: True)

    definition = JobDefinition(
        module_name='job',
        service_name='budget-job',
        run_once=True,
        execution_timeout_seconds=60,
        shutdown_grace_seconds=10,
        iteration_timeout_seconds=20,
        lease_wait_seconds=None,
        resource_sample_seconds=1,
    )

    def iteration(context) -> None:
        observed_remaining.append(context.safe_remaining_seconds)
        now[0] += 1

    result = execute_job(
        definition=definition,
        iteration=iteration,
        argv=[],
        environ=_environment(tmp_path, environment='dev'),
    )

    assert captured_wait == [30]
    assert observed_remaining == [30]
    assert result.duration_seconds == 21


def test_azure_observability_projection_excludes_unrelated_secrets() -> None:
    projected = _project_azure_observability_environ(
        {
            'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'export',
            'ATLANTICUS_AZURE_OBSERVABILITY_PROFILE': 'diagnostic',
            'APPLICATION_INSIGHTS_CONNECTION_STRING': 'InstrumentationKey=secret',
            'COSMOS_KEY_OPERATIONAL': 'secret',
        }
    )

    assert projected == {
        'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'export',
        'ATLANTICUS_AZURE_OBSERVABILITY_PROFILE': 'diagnostic',
        'APPLICATION_INSIGHTS_CONNECTION_STRING': 'InstrumentationKey=secret',
    }


def test_execute_job_writes_one_work_iteration_and_one_execution_summary(tmp_path, capsys) -> None:
    def iteration(context) -> None:
        context.mark_iteration_work()
        context.set_iteration_fact('table', 'std_shift_dumps')
        context.set_iteration_fact('new_data', True)
        context.set_execution_fact('new_data', True)
        context.increment_execution_counter('rows', 120)

    result = execute_job(
        definition=_definition(),
        iteration=iteration,
        argv=[],
        environ=_environment(tmp_path),
    )

    directory = _day_directory(tmp_path)
    iterations = [
        json.loads(line) for line in (directory / 'iterations.jsonl').read_text().splitlines()
    ]
    executions = [
        json.loads(line) for line in (directory / 'executions.jsonl').read_text().splitlines()
    ]
    console = capsys.readouterr().out

    assert result.iteration_count == 1
    assert len(iterations) == 1
    assert iterations[0]['event'] == 'iteration.completed'
    assert iterations[0]['table'] == 'std_shift_dumps'
    assert len(executions) == 1
    assert executions[0]['event'] == 'execution.completed'
    assert executions[0]['work_iterations'] == 1
    assert executions[0]['empty_iterations'] == 0
    assert executions[0]['rows'] == 120
    assert 'dispatch-ingestion-job started' in console
    assert 'dispatch-ingestion-job completed' in console
    assert 'new_data=true' in console
    assert '"context"' not in console
    assert not (directory / 'events.jsonl').exists()
    assert not (tmp_path / 'ada' / '.runtime' / 'leases' / 'dispatch-ingestion-job.json').exists()


def test_empty_iteration_is_counted_but_not_persisted_as_iteration(tmp_path) -> None:
    execute_job(
        definition=_definition(),
        iteration=lambda context: None,
        argv=[],
        environ=_environment(tmp_path, environment='dev'),
    )

    directory = _day_directory(tmp_path)
    assert not (directory / 'iterations.jsonl').exists()
    execution = json.loads((directory / 'executions.jsonl').read_text().splitlines()[0])
    assert execution['work_iterations'] == 0
    assert execution['empty_iterations'] == 1


def test_preview_contains_same_operational_log_contract(tmp_path) -> None:
    environment = _environment(tmp_path, environment='dev')
    environment.update(
        {
            'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'preview',
            'ATLANTICUS_AZURE_OBSERVABILITY_PROFILE': 'slim',
        }
    )

    def iteration(context) -> None:
        context.mark_iteration_work()
        context.set_iteration_fact('rows', 25)
        context.increment_execution_counter('rows', 25)

    execute_job(
        definition=_definition(),
        iteration=iteration,
        argv=[],
        environ=environment,
    )

    directory = _day_directory(tmp_path)
    records = [
        json.loads(line) for line in (directory / 'azure-preview.jsonl').read_text().splitlines()
    ]
    assert [record['event'] for record in records] == [
        'execution.started',
        'iteration.completed',
        'execution.completed',
    ]
    assert records[0]['application'] == 'ada'
    assert records[0]['environment'] == 'dev'
    assert records[1]['rows'] == 25
    assert records[2]['rows'] == 25


def test_invalid_azure_configuration_is_recorded_as_warning_issue(tmp_path) -> None:
    environment = _environment(tmp_path, environment='dev')
    environment['ATLANTICUS_AZURE_OBSERVABILITY_MODE'] = 'export'

    execute_job(
        definition=_definition(),
        iteration=lambda context: None,
        argv=[],
        environ=environment,
    )

    issues = [
        json.loads(line)
        for line in (_day_directory(tmp_path) / 'issues.jsonl').read_text().splitlines()
    ]
    assert issues[0]['event'] == 'observability.azure.bootstrap.failed'
    assert issues[0]['level'] == 'warning'
    assert issues[0]['error_type']


def test_recovered_lease_records_previous_execution_timeout(tmp_path, capsys) -> None:
    definition = _definition()
    stale = ExecutionLease(
        volume_path=tmp_path,
        application='ada',
        service_name=definition.service_name,
        module_name=definition.module_name,
        run_id='previous-run',
        lease_timeout_seconds=12,
        wait_seconds=0,
    )
    stale.acquire()
    payload = json.loads(stale.path.read_text())
    payload['expires_at_utc'] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    stale.path.write_text(json.dumps(payload))

    execute_job(
        definition=definition,
        iteration=lambda context: None,
        argv=[],
        environ=_environment(tmp_path),
    )
    capsys.readouterr()

    executions = [
        json.loads(line)
        for line in (_day_directory(tmp_path) / 'executions.jsonl').read_text().splitlines()
    ]
    assert executions[0]['event'] == 'execution.timed_out'
    assert executions[0]['run_id'] == 'previous-run'
    assert executions[1]['event'] == 'execution.completed'


def test_failed_iteration_records_one_operational_failure_with_diagnostic_reference(
    tmp_path,
    capsys,
) -> None:
    signed_url = 'https://account.blob.core.windows.net/data?sig=secret-value'

    def fail(context) -> None:
        raise ValueError(signed_url)

    with pytest.raises(ValueError, match='secret-value'):
        execute_job(
            definition=_definition(),
            iteration=fail,
            argv=[],
            environ=_environment(tmp_path),
        )
    console = capsys.readouterr().err

    execution = json.loads(
        (_day_directory(tmp_path) / 'executions.jsonl').read_text().splitlines()[0]
    )
    issue = json.loads((_day_directory(tmp_path) / 'issues.jsonl').read_text().splitlines()[0])
    assert execution['event'] == 'execution.failed'
    assert execution['error_type'] == 'ValueError'
    assert execution['diagnostic_available'] is True
    assert execution['failed_iteration'] == 1
    assert issue['traceback']
    assert signed_url not in console
    assert signed_url not in json.dumps(execution)
    assert signed_url not in json.dumps(issue)
    lease_path = tmp_path / 'ada' / '.runtime' / 'leases' / 'dispatch-ingestion-job.json'
    assert not lease_path.exists()


def test_keyboard_interrupt_is_recorded_as_controlled_cancellation(tmp_path, capsys) -> None:
    def interrupt(context) -> None:
        raise KeyboardInterrupt

    result = execute_job(
        definition=_definition(),
        iteration=interrupt,
        argv=[],
        environ=_environment(tmp_path),
    )
    console = capsys.readouterr().out

    execution = json.loads(
        (_day_directory(tmp_path) / 'executions.jsonl').read_text().splitlines()[0]
    )
    assert result.status.value == 'warning'
    assert result.stop_reason == 'interrupted'
    assert execution['event'] == 'execution.cancelled'
    assert execution['status'] == 'warning'
    assert execution['stop_reason'] == 'interrupted'
    assert execution['cancelled_iteration'] == 1
    assert 'interrupted' in console
    assert not (_day_directory(tmp_path) / 'issues.jsonl').exists()


def test_runtime_cancellation_is_not_recorded_as_failure(tmp_path) -> None:
    def cancel(context) -> None:
        raise RuntimeCancellationRequested('requested')

    result = execute_job(
        definition=_definition(),
        iteration=cancel,
        argv=[],
        environ=_environment(tmp_path),
    )

    execution = json.loads(
        (_day_directory(tmp_path) / 'executions.jsonl').read_text().splitlines()[0]
    )
    assert result.status.value == 'warning'
    assert result.stop_reason == 'requested'
    assert execution['event'] == 'execution.cancelled'
    assert not (_day_directory(tmp_path) / 'issues.jsonl').exists()


def test_sigterm_requests_cooperative_stop_and_restores_previous_handler(tmp_path) -> None:
    previous_handler = signal.getsignal(signal.SIGTERM)

    def request_sigterm(context) -> None:
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)

    result = execute_job(
        definition=_definition(),
        iteration=request_sigterm,
        argv=[],
        environ=_environment(tmp_path),
    )

    assert result.status.value == 'warning'
    assert result.stop_reason == 'sigterm'
    assert signal.getsignal(signal.SIGTERM) == previous_handler


def test_lost_lease_stops_business_and_fails_execution(tmp_path) -> None:
    definition = JobDefinition(
        module_name='lease_loss_job',
        service_name='lease-loss-job',
        run_once=True,
        iteration_timeout_seconds=1,
        execution_timeout_seconds=2,
        shutdown_grace_seconds=0.2,
        lease_timeout_seconds=0.2,
        lease_renew_seconds=0.02,
        lease_wait_seconds=0,
        resource_sample_seconds=0.1,
    )

    def lose_lease(context) -> None:
        path = tmp_path / 'ada' / '.runtime' / 'leases' / 'lease-loss-job.json'
        guard_path = path.parent / '.lease-loss-job.recovery'
        descriptor = os.open(guard_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            payload = json.loads(path.read_text())
            payload['expires_at_utc'] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            path.write_text(json.dumps(payload))
        finally:
            os.close(descriptor)
            guard_path.unlink(missing_ok=True)
        assert not context.wait(1)
        context.raise_if_cancelled()

    with pytest.raises(LeaseOwnershipLostError, match='ownership'):
        execute_job(
            definition=definition,
            iteration=lose_lease,
            argv=[],
            environ=_environment(tmp_path),
        )

    execution = json.loads(
        (
            tmp_path
            / 'ada/logs/lease-loss-job'
            / f'day={datetime.now(UTC).date().isoformat()}'
            / 'executions.jsonl'
        )
        .read_text()
        .splitlines()[0]
    )
    assert execution['event'] == 'execution.failed'
    assert execution['error_type'] == 'LeaseOwnershipLostError'


def test_release_failure_does_not_hide_business_error(tmp_path, monkeypatch) -> None:
    def fail(context) -> None:
        raise ValueError('business failure')

    def fail_release(self, *, completed: bool = False) -> bool:
        raise OSError('lease cleanup failed')

    monkeypatch.setattr(ExecutionLease, 'release', fail_release)

    with pytest.raises(ValueError, match='business failure'):
        execute_job(
            definition=_definition(),
            iteration=fail,
            argv=[],
            environ=_environment(tmp_path),
        )


def test_resource_sampling_failure_does_not_break_job(tmp_path, monkeypatch) -> None:
    def fail_sample(self):
        raise RuntimeError('sampling failure')

    monkeypatch.setattr(CgroupResourceSampler, 'sample', fail_sample)

    result = execute_job(
        definition=_definition(),
        iteration=lambda context: None,
        argv=[],
        environ=_environment(tmp_path),
    )

    assert result.status.value == 'success'
    issues = [
        json.loads(line)
        for line in (_day_directory(tmp_path) / 'issues.jsonl').read_text().splitlines()
    ]
    assert issues[0]['event'] == 'resource.monitor.failed'
    assert issues[0]['level'] == 'warning'


def test_iteration_can_override_static_sleep_with_adaptive_delay(tmp_path, monkeypatch) -> None:
    observed_waits: list[float] = []
    iterations: list[int] = []

    def fake_wait(self, seconds: float) -> bool:
        observed_waits.append(seconds)
        return True

    monkeypatch.setattr(JobRuntimeContext, 'wait', fake_wait)

    definition = JobDefinition(
        module_name='adaptive_job',
        service_name='adaptive-job',
        sleep_seconds=5,
        iteration_timeout_seconds=5,
        execution_timeout_seconds=20,
        shutdown_grace_seconds=2,
        lease_timeout_seconds=12,
        lease_wait_seconds=0,
        resource_sample_seconds=0.01,
    )

    def iteration(context: JobRuntimeContext) -> None:
        iterations.append(context.iteration)
        if context.iteration == 1:
            context.set_next_iteration_delay(0.25)
            return
        context.request_stop('requested')

    result = execute_job(
        definition=definition,
        iteration=iteration,
        argv=[],
        environ=_environment(tmp_path),
    )

    assert iterations == [1, 2]
    assert observed_waits == [0.25]
    assert result.stop_reason == 'requested'


def test_execute_job_can_disable_file_logs_without_disabling_console(tmp_path, capsys) -> None:
    environment = _environment(tmp_path)
    environment['ATLANTICUS_OBSERVABILITY_FILE_LOGS_ENABLED'] = 'false'

    execute_job(
        definition=_definition(),
        iteration=lambda context: context.mark_iteration_work(),
        argv=[],
        environ=environment,
    )

    console = capsys.readouterr().out
    assert 'dispatch-ingestion-job started' in console
    assert 'dispatch-ingestion-job completed' in console
    assert not (tmp_path / 'ada' / 'logs').exists()


def test_scheduled_run_once_uses_effective_window_without_changing_iteration_policy(
    tmp_path,
    monkeypatch,
) -> None:
    import atlanticus.runtime.context as context_module

    monkeypatch.setattr(
        context_module,
        '_utc_now',
        lambda: datetime(2026, 8, 23, 21, 10, 5, tzinfo=UTC),
    )
    observed: list[tuple[str, datetime | None, datetime]] = []
    definition = JobDefinition(
        module_name='scheduled_job',
        service_name='scheduled-job',
        run_once=True,
        iteration_timeout_seconds=60,
        execution_timeout_seconds=600,
        shutdown_grace_seconds=20,
        lease_timeout_seconds=120,
        lease_wait_seconds=0,
        resource_sample_seconds=0.01,
    )
    environ = {
        **_environment(tmp_path),
        'ATLANTICUS_JOB_SCHEDULE_CRON': '*/10 * * * *',
        'ATLANTICUS_JOB_PLATFORM_TIMEOUT_SECONDS': '120',
    }

    def iteration(context: JobRuntimeContext) -> None:
        observed.append(
            (
                context.execution_mode,
                context.scheduled_at_utc,
                context.deadline_utc,
            )
        )

    result = execute_job(
        definition=definition,
        iteration=iteration,
        argv=[],
        environ=environ,
    )

    assert result.iteration_count == 1
    assert result.stop_reason == 'run_once'
    assert observed == [
        (
            'scheduled_external',
            datetime(2026, 8, 23, 21, 10, 0, tzinfo=UTC),
            datetime(2026, 8, 23, 21, 12, 5, tzinfo=UTC),
        )
    ]


def test_scheduled_run_once_skips_business_work_when_effective_window_is_too_short(
    tmp_path,
    monkeypatch,
) -> None:
    import atlanticus.runtime.context as context_module

    monkeypatch.setattr(
        context_module,
        '_utc_now',
        lambda: datetime(2026, 8, 23, 21, 10, 5, tzinfo=UTC),
    )
    calls: list[int] = []
    definition = JobDefinition(
        module_name='scheduled_job',
        service_name='scheduled-job',
        run_once=True,
        iteration_timeout_seconds=60,
        execution_timeout_seconds=600,
        shutdown_grace_seconds=20,
        lease_timeout_seconds=120,
        lease_wait_seconds=0,
        resource_sample_seconds=0.01,
    )
    environ = {
        **_environment(tmp_path),
        'ATLANTICUS_JOB_SCHEDULE_CRON': '*/10 * * * *',
        'ATLANTICUS_JOB_PLATFORM_TIMEOUT_SECONDS': '50',
    }

    result = execute_job(
        definition=definition,
        iteration=lambda context: calls.append(context.iteration),
        argv=[],
        environ=environ,
    )

    assert calls == []
    assert result.iteration_count == 0
    assert result.stop_reason == 'insufficient_remaining_time'


def test_scheduled_lease_wait_uses_the_full_safe_window(tmp_path) -> None:
    definition = JobDefinition(
        module_name='job',
        service_name='scheduled-wait-job',
        execution_timeout_seconds=600,
        shutdown_grace_seconds=20,
        iteration_timeout_seconds=60,
        lease_wait_seconds=None,
    )
    environment = _environment(tmp_path)
    environment['ATLANTICUS_JOB_SCHEDULE_CRON'] = '*/10 * * * *'
    configuration = RuntimeConfiguration.from_sources(environ=environment)
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='11111111-1111-1111-1111-111111111111',
        correlation_id='22222222-2222-2222-2222-222222222222',
        clock=lambda: 100.0,
        wall_clock=lambda: datetime(2026, 8, 23, 21, 10, 0, tzinfo=UTC),
    )

    assert context.safe_remaining_seconds == 580
    assert _effective_lease_wait_seconds(definition, context) == 580


def test_scheduled_slot_is_deduplicated_after_successful_execution(
    tmp_path,
    monkeypatch,
) -> None:
    import atlanticus.runtime.context as context_module

    fixed_now = datetime(2026, 8, 23, 21, 10, 5, tzinfo=UTC)
    monkeypatch.setattr(context_module, '_utc_now', lambda: fixed_now)
    definition = JobDefinition(
        module_name='scheduled_job',
        service_name='scheduled-job',
        run_once=True,
        iteration_timeout_seconds=5,
        execution_timeout_seconds=600,
        shutdown_grace_seconds=20,
        lease_timeout_seconds=30,
        lease_wait_seconds=0,
        resource_sample_seconds=0.01,
    )
    environment = _environment(tmp_path)
    environment['ATLANTICUS_JOB_SCHEDULE_CRON'] = '*/10 * * * *'
    calls = []

    def iteration(context) -> None:
        calls.append(context.iteration)

    first = execute_job(
        definition=definition,
        iteration=iteration,
        argv=[],
        environ=environment,
    )
    second = execute_job(
        definition=definition,
        iteration=iteration,
        argv=[],
        environ=environment,
    )

    assert first.stop_reason == 'run_once'
    assert first.iteration_count == 1
    assert second.stop_reason == 'scheduled_slot_completed'
    assert second.iteration_count == 0
    assert calls == [1]
    authority_path = tmp_path / 'ada' / '.runtime' / 'authority' / 'scheduled-job.json'
    authority = json.loads(authority_path.read_text(encoding='utf-8'))
    assert authority['generation'] == 1
    assert authority['last_completed_scheduled_at_utc'] == '2026-08-23T21:10:00+00:00'
