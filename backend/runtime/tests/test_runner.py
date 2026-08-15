from __future__ import annotations

import json
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
    monkeypatch.setattr(ExecutionLease, 'release', lambda self: True)

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
        payload = json.loads(path.read_text())
        payload['owner_token'] = 'different-owner'
        path.write_text(json.dumps(payload))
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

    def fail_release(self) -> bool:
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
