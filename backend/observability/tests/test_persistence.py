from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from atlanticus.kernel import DataSanitizer, OperationStatus
from atlanticus.observability import (
    DailyTraceSink,
    ErrorInfo,
    EventAudience,
    EventCategory,
    EventSeverity,
    ObservabilityEvent,
    ObservabilitySettings,
)


def _settings() -> ObservabilitySettings:
    return ObservabilitySettings.build(
        application='etl app',
        service='daily/load',
        module='daily_load',
        environment='local',
        instance_id='container:1',
        process_id=101,
    )


def _directory(tmp_path, day: str):
    return tmp_path / 'etl_app' / 'logs' / 'daily_load' / f'day={day}'


def test_daily_sink_persists_only_operational_histories_and_root_latest(tmp_path) -> None:
    sink = DailyTraceSink(tmp_path)
    settings = _settings()
    sanitizer = DataSanitizer()
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    context = replace(settings.base_context(), run_id='run-1', iteration=1)

    sink.emit(
        ObservabilityEvent(
            name='dependency.started',
            category=EventCategory.DEPENDENCY,
            context=context,
            attributes={'connection_string': 'secret'},
            occurred_at_utc=now,
        ),
        settings,
        sanitizer,
    )
    sink.emit(
        ObservabilityEvent(
            name='runtime.iteration.summary',
            category=EventCategory.ITERATION,
            audience=EventAudience.OPERATIONS,
            status=OperationStatus.SUCCESS,
            context=context,
            duration_ms=1200,
            attributes={'rows': 10, 'new_data': True},
            occurred_at_utc=now + timedelta(seconds=1),
        ),
        settings,
        sanitizer,
    )
    sink.emit(
        ObservabilityEvent(
            name='runtime.execution.summary',
            category=EventCategory.LIFECYCLE,
            audience=EventAudience.OPERATIONS,
            status=OperationStatus.SUCCESS,
            context=replace(settings.base_context(), run_id='run-1'),
            duration_ms=5000,
            metrics={
                'iterations': 2,
                'work_iterations': 1,
                'empty_iterations': 1,
                'cpu_peak_percent': 22.5,
                'memory_peak_percent': 3.1,
            },
            attributes={'new_data': True},
            occurred_at_utc=now + timedelta(seconds=5),
        ),
        settings,
        sanitizer,
    )

    directory = _directory(tmp_path, '2026-08-10')
    assert not (directory / 'events.jsonl').exists()
    iteration = json.loads((directory / 'iterations.jsonl').read_text().splitlines()[0])
    execution = json.loads((directory / 'executions.jsonl').read_text().splitlines()[0])
    latest = json.loads((directory.parent / 'latest.json').read_text())
    summary = json.loads((directory / 'daily-summary.json').read_text())

    assert iteration['event'] == 'iteration.completed'
    assert iteration['application'] == 'etl app'
    assert iteration['environment'] == 'local'
    assert iteration['service'] == 'daily/load'
    assert iteration['rows'] == 10
    assert execution['event'] == 'execution.completed'
    assert execution['work_iterations'] == 1
    assert latest == execution
    assert summary['executions'] == 1
    assert summary['successful'] == 1
    assert summary['work_iterations'] == 1
    assert summary['cpu_peak_percent'] == 22.5


def test_issue_file_keeps_reason_and_traceback_without_repeating_context(tmp_path) -> None:
    sink = DailyTraceSink(tmp_path)
    settings = _settings()
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)

    sink.emit(
        ObservabilityEvent(
            name='execution.failed',
            category=EventCategory.LIFECYCLE,
            audience=EventAudience.OPERATIONS,
            severity=EventSeverity.ERROR,
            status=OperationStatus.ERROR,
            context=replace(settings.base_context(), run_id='run-error'),
            message='Execution failed',
            error=ErrorInfo(
                error_type='ServiceBusReceiveError',
                message='Could not receive Service Bus message',
                traceback='traceback-details',
                cause_type='ServiceBusAuthenticationError',
                cause_message='CBS Token authentication failed',
            ),
            occurred_at_utc=now,
        ),
        settings,
        DataSanitizer(),
    )

    issue = json.loads(
        (_directory(tmp_path, '2026-08-10') / 'issues.jsonl').read_text().splitlines()[0]
    )
    assert issue['error_type'] == 'ServiceBusReceiveError'
    assert issue['cause_type'] == 'ServiceBusAuthenticationError'
    assert issue['cause_message'] == 'CBS Token authentication failed'
    assert issue['diagnostic_available'] is True
    assert issue['diagnostic_ref'] == 'run:run-error'
    assert issue['traceback'] == 'traceback-details'
    assert 'context' not in issue
    assert 'attributes' not in issue
    assert 'metrics' not in issue


def test_first_event_of_new_iso_week_purges_previous_week_but_keeps_latest(tmp_path) -> None:
    sink = DailyTraceSink(tmp_path)
    settings = _settings()
    sanitizer = DataSanitizer()

    sink.emit(
        ObservabilityEvent(
            name='runtime.execution.summary',
            category=EventCategory.LIFECYCLE,
            audience=EventAudience.OPERATIONS,
            status=OperationStatus.SUCCESS,
            context=replace(settings.base_context(), run_id='week-33'),
            occurred_at_utc=datetime(2026, 8, 16, 12, tzinfo=UTC),
        ),
        settings,
        sanitizer,
    )
    service_root = tmp_path / 'etl_app' / 'logs' / 'daily_load'
    previous_day = service_root / 'day=2026-08-16'
    (previous_day / 'azure-diagnostic-spans.jsonl').write_text('{}\n', encoding='utf-8')

    sink.emit(
        ObservabilityEvent(
            name='runtime.execution.summary',
            category=EventCategory.LIFECYCLE,
            audience=EventAudience.OPERATIONS,
            status=OperationStatus.SUCCESS,
            context=replace(settings.base_context(), run_id='week-34'),
            occurred_at_utc=datetime(2026, 8, 17, 0, 1, tzinfo=UTC),
        ),
        settings,
        sanitizer,
    )

    assert not previous_day.exists()
    assert (service_root / 'day=2026-08-17').exists()
    latest = json.loads((service_root / 'latest.json').read_text())
    assert latest['run_id'] == 'week-34'


def test_iteration_append_does_not_rewrite_daily_summary(tmp_path) -> None:
    sink = DailyTraceSink(tmp_path)
    settings = _settings()
    occurred_at = datetime(2026, 8, 10, 12, tzinfo=UTC)

    sink.emit(
        ObservabilityEvent(
            name='runtime.iteration.summary',
            category=EventCategory.ITERATION,
            audience=EventAudience.OPERATIONS,
            status=OperationStatus.SUCCESS,
            context=replace(settings.base_context(), run_id='run-1', iteration=1),
            attributes={'rows': 10},
            occurred_at_utc=occurred_at,
        ),
        settings,
        DataSanitizer(),
    )

    directory = _directory(tmp_path, '2026-08-10')
    assert (directory / 'iterations.jsonl').exists()
    assert not (directory / 'daily-summary.json').exists()
