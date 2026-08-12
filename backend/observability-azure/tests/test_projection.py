from __future__ import annotations

from atlanticus.kernel import DataSanitizer, OperationStatus
from atlanticus.observability import (
    EventAudience,
    EventCategory,
    EventSeverity,
    ObservabilityEvent,
    ObservabilitySettings,
    OperationalEventProjection,
)


def _settings() -> ObservabilitySettings:
    return ObservabilitySettings.build(
        application='ada_operaciones_integradas',
        service='notpii',
        module='ada.processes.notpii',
        environment='dev',
        instance_id='container-id',
        process_id=42,
    )


def _project(event: ObservabilityEvent):
    payload = event.to_dict(settings=_settings(), sanitizer=DataSanitizer())
    return OperationalEventProjection().project(event, payload)


def test_operational_projection_is_flat_and_keeps_shared_identity() -> None:
    event = ObservabilityEvent(
        name='runtime.execution.summary',
        category=EventCategory.LIFECYCLE,
        audience=EventAudience.OPERATIONS,
        status=OperationStatus.SUCCESS,
        metrics={'messages_received': 12},
        attributes={
            'new_data': True,
            'connection_string': 'secret',
            'max_wait_time_seconds': 10,
        },
    )

    projected = _project(event)

    assert projected is not None
    assert projected['application'] == 'ada_operaciones_integradas'
    assert projected['environment'] == 'dev'
    assert projected['service'] == 'notpii'
    assert projected['event'] == 'execution.completed'
    assert projected['messages_received'] == 12
    assert projected['new_data'] is True
    assert 'context' not in projected
    assert 'attributes' not in projected
    assert 'metrics' not in projected
    assert 'connection_string' not in projected
    assert 'max_wait_time_seconds' not in projected


def test_dependency_started_is_noise_but_failed_is_operational() -> None:
    started = ObservabilityEvent(
        name='dependency.started',
        category=EventCategory.DEPENDENCY,
    )
    failed = ObservabilityEvent(
        name='dependency.failed',
        category=EventCategory.DEPENDENCY,
        severity=EventSeverity.ERROR,
    )

    assert _project(started) is None
    projected = _project(failed)
    assert projected is not None
    assert projected['event'] == 'dependency.failed'
    assert projected['level'] == 'error'
