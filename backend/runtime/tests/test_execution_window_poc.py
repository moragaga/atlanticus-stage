from __future__ import annotations

from datetime import UTC, datetime

from atlanticus.runtime import JobDefinition, JobRuntimeContext, RuntimeConfiguration
from atlanticus.runtime._schedule import resolve_schedule_slot


def _definition() -> JobDefinition:
    return JobDefinition(
        module_name='poc.job',
        service_name='poc-job',
        run_once=True,
        iteration_timeout_seconds=60,
        execution_timeout_seconds=600,
        shutdown_grace_seconds=20,
        lease_timeout_seconds=120,
    )


def _configuration(tmp_path, **values: str) -> RuntimeConfiguration:
    environ = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'poc',
        'VOLUMEN_PATH': str(tmp_path),
        **values,
    }
    return RuntimeConfiguration.from_sources(environ=environ)


def test_poc_relative_mode_preserves_current_execution_budget(tmp_path) -> None:
    context = JobRuntimeContext.create(
        definition=_definition(),
        configuration=_configuration(tmp_path),
        run_id='run-1',
        correlation_id='correlation-1',
        clock=lambda: 100.0,
        wall_clock=lambda: datetime(2026, 8, 23, 21, 16, 0, tzinfo=UTC),
    )

    assert context.execution_mode == 'relative'
    assert context.scheduled_at_utc is None
    assert context.deadline_monotonic == 700
    assert context.safe_deadline_monotonic == 680
    assert context.deadline_utc == datetime(2026, 8, 23, 21, 26, 0, tzinfo=UTC)


def test_poc_schedule_resolves_current_and_next_slot_without_fixed_scheduled_at() -> None:
    slot = resolve_schedule_slot(
        expression='*/10 * * * *',
        timezone_name='UTC',
        now_utc=datetime(2026, 8, 23, 21, 13, 7, tzinfo=UTC),
    )

    assert slot.scheduled_at_utc == datetime(2026, 8, 23, 21, 10, 0, tzinfo=UTC)
    assert slot.next_scheduled_at_utc == datetime(2026, 8, 23, 21, 20, 0, tzinfo=UTC)


def test_poc_late_scheduled_start_does_not_receive_a_new_full_window(tmp_path) -> None:
    context = JobRuntimeContext.create(
        definition=_definition(),
        configuration=_configuration(
            tmp_path,
            ATLANTICUS_JOB_SCHEDULE_CRON='*/10 * * * *',
            ATLANTICUS_JOB_SCHEDULE_TIMEZONE='UTC',
        ),
        run_id='run-1',
        correlation_id='correlation-1',
        clock=lambda: 100.0,
        wall_clock=lambda: datetime(2026, 8, 23, 21, 16, 0, tzinfo=UTC),
    )

    assert context.execution_mode == 'scheduled_external'
    assert context.scheduled_at_utc == datetime(2026, 8, 23, 21, 10, 0, tzinfo=UTC)
    assert context.next_scheduled_at_utc == datetime(2026, 8, 23, 21, 20, 0, tzinfo=UTC)
    assert context.deadline_utc == datetime(2026, 8, 23, 21, 20, 0, tzinfo=UTC)
    assert context.safe_deadline_utc == datetime(2026, 8, 23, 21, 19, 40, tzinfo=UTC)
    assert context.deadline_monotonic == 340
    assert context.safe_deadline_monotonic == 320


def test_poc_platform_timeout_can_be_stricter_than_cron_and_runtime_timeout(tmp_path) -> None:
    context = JobRuntimeContext.create(
        definition=_definition(),
        configuration=_configuration(
            tmp_path,
            ATLANTICUS_JOB_SCHEDULE_CRON='0 */2 * * *',
            ATLANTICUS_JOB_SCHEDULE_TIMEZONE='UTC',
            ATLANTICUS_JOB_PLATFORM_TIMEOUT_SECONDS='300',
        ),
        run_id='run-1',
        correlation_id='correlation-1',
        clock=lambda: 500.0,
        wall_clock=lambda: datetime(2026, 8, 23, 10, 0, 0, tzinfo=UTC),
    )

    assert context.scheduled_at_utc == datetime(2026, 8, 23, 10, 0, 0, tzinfo=UTC)
    assert context.next_scheduled_at_utc == datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
    assert context.platform_deadline_utc == datetime(2026, 8, 23, 10, 5, 0, tzinfo=UTC)
    assert context.deadline_utc == datetime(2026, 8, 23, 10, 5, 0, tzinfo=UTC)
    assert context.safe_deadline_utc == datetime(2026, 8, 23, 10, 4, 40, tzinfo=UTC)
    assert context.deadline_monotonic == 800
    assert context.safe_deadline_monotonic == 780


def test_poc_run_once_remains_iteration_policy_not_window_policy(tmp_path) -> None:
    context = JobRuntimeContext.create(
        definition=_definition(),
        configuration=_configuration(
            tmp_path,
            ATLANTICUS_JOB_SCHEDULE_CRON='*/10 * * * *',
            ATLANTICUS_JOB_PLATFORM_TIMEOUT_SECONDS='120',
        ),
        run_id='run-1',
        correlation_id='correlation-1',
        clock=lambda: 1000.0,
        wall_clock=lambda: datetime(2026, 8, 23, 21, 10, 5, tzinfo=UTC),
    )

    assert context.definition.run_once is True
    assert context.execution_mode == 'scheduled_external'
    assert context.deadline_monotonic == 1120
    assert context.safe_deadline_monotonic == 1100
