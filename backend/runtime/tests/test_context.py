from __future__ import annotations

import pytest

from atlanticus.runtime import (
    JobDefinition,
    JobRuntimeContext,
    RuntimeCancellationRequested,
    RuntimeConfiguration,
)


def test_context_exposes_budget_memory_and_cooperative_stop(tmp_path) -> None:
    now = [10.0]

    def clock() -> float:
        return now[0]

    definition = JobDefinition(
        module_name='job',
        service_name='job-service',
        execution_timeout_seconds=20,
        shutdown_grace_seconds=5,
        iteration_timeout_seconds=10,
        lease_timeout_seconds=30,
    )
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='run-1',
        correlation_id='correlation-1',
        clock=clock,
    )

    assert context.safe_remaining_seconds == 15
    assert context.get_or_create('catalog', lambda: {'loaded': True}) == {'loaded': True}
    assert context.logger._component == 'job'
    assert context.application_root == tmp_path / 'ada'

    now[0] = 25.0
    assert context.should_stop
    with pytest.raises(RuntimeCancellationRequested):
        context.raise_if_cancelled()


def test_context_accumulates_custom_facts_without_emitting_events(tmp_path) -> None:
    definition = JobDefinition(
        module_name='job',
        service_name='job-service',
        execution_timeout_seconds=20,
        shutdown_grace_seconds=5,
        iteration_timeout_seconds=10,
        lease_timeout_seconds=30,
    )
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='run-1',
        correlation_id='correlation-1',
    )

    context._begin_iteration(1)
    context.mark_iteration_work()
    context.set_iteration_fact('new_data', True)
    context.increment_iteration_counter('rows', 2)
    context.set_execution_fact('new_data', True)
    context.increment_execution_counter('rows', 2)

    assert context.iteration_has_work is True
    assert context._iteration_facts() == {'new_data': True, 'rows': 2}
    assert context._execution_facts() == {'new_data': True, 'rows': 2}

    with pytest.raises(ValueError, match='reserved'):
        context.set_execution_fact('run_id', 'invalid')
    with pytest.raises(ValueError, match='reserved'):
        context.set_execution_fact('stop_reason', 'invalid')
    with pytest.raises(ValueError, match='sensitive'):
        context.set_execution_fact('connection_string', 'invalid')


def test_context_normalizes_memory_keys_and_preserves_first_stop_reason(tmp_path) -> None:
    definition = JobDefinition(
        module_name='ada.process',
        service_name='process',
        execution_timeout_seconds=20,
        shutdown_grace_seconds=5,
        iteration_timeout_seconds=10,
        lease_timeout_seconds=30,
    )
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='run-1',
        correlation_id='correlation-1',
    )

    context.set_memory(' catalog ', {'loaded': True})
    context.request_stop('sigterm')
    context.request_stop('requested')

    assert context.get_memory('catalog') == {'loaded': True}
    assert context.get_memory(' catalog ') == {'loaded': True}
    assert context.stop_reason == 'sigterm'


@pytest.mark.parametrize('seconds', [float('nan'), float('inf'), True, -1])
def test_context_rejects_invalid_waits(tmp_path, seconds) -> None:
    definition = JobDefinition(
        module_name='job',
        service_name='job',
        execution_timeout_seconds=20,
        shutdown_grace_seconds=5,
        iteration_timeout_seconds=10,
        lease_timeout_seconds=30,
    )
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='run-1',
        correlation_id='correlation-1',
    )

    with pytest.raises((TypeError, ValueError)):
        context.wait(seconds)


def test_context_accepts_per_iteration_delay_and_resets_it(tmp_path) -> None:
    definition = JobDefinition(
        module_name='job',
        service_name='job',
        execution_timeout_seconds=20,
        shutdown_grace_seconds=5,
        iteration_timeout_seconds=10,
        lease_timeout_seconds=30,
    )
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='run-1',
        correlation_id='correlation-1',
    )

    context._begin_iteration(1)
    context.set_next_iteration_delay(8.75)
    assert context._next_iteration_delay() == 8.75

    context._begin_iteration(2)
    assert context._next_iteration_delay() is None


@pytest.mark.parametrize('seconds', [float('nan'), float('inf'), True, -1])
def test_context_rejects_invalid_next_iteration_delays(tmp_path, seconds) -> None:
    definition = JobDefinition(
        module_name='job',
        service_name='job',
        execution_timeout_seconds=20,
        shutdown_grace_seconds=5,
        iteration_timeout_seconds=10,
        lease_timeout_seconds=30,
    )
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='run-1',
        correlation_id='correlation-1',
    )

    with pytest.raises((TypeError, ValueError)):
        context.set_next_iteration_delay(seconds)
