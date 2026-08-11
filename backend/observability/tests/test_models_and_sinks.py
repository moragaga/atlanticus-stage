from __future__ import annotations

from io import StringIO

from atlanticus.kernel import DataSanitizer
from atlanticus.observability import (
    EventAudience,
    EventCategory,
    EventSeverity,
    FilteredEventProjection,
    MemoryEventSink,
    ObservabilityEvent,
    ObservabilitySettings,
)


def _settings() -> ObservabilitySettings:
    return ObservabilitySettings.build(
        application='payments',
        service='daily-job',
        module='daily_job',
        environment='local',
        instance_id='test-instance',
        process_id=42,
    )


def test_event_materialization_sanitizes_sensitive_values() -> None:
    event = ObservabilityEvent(
        name='data.downloaded',
        category=EventCategory.DATA,
        metrics={'record_count': 12},
        attributes={'access_token': 'secret', 'container': 'source'},
    )

    payload = event.to_dict(settings=_settings(), sanitizer=DataSanitizer())

    assert payload['metrics'] == {'record_count': 12}
    assert payload['attributes'] == {
        'access_token': '***redacted***',
        'container': 'source',
    }
    assert payload['context']['instance_id'] == 'test-instance'
    assert payload['context']['process_id'] == 42
    assert payload['context']['module'] == 'daily_job'
    assert payload['audience'] == 'local'


def test_operational_audience_is_part_of_the_event_contract() -> None:
    event = ObservabilityEvent(
        name='kpi.evaluation.failed',
        category=EventCategory.DATA,
        audience=EventAudience.OPERATIONS,
    )

    payload = event.to_dict(settings=_settings(), sanitizer=DataSanitizer())

    assert payload['schema_version'] == 2
    assert payload['audience'] == 'operations'


def test_filtered_projection_removes_local_only_fields() -> None:
    sink = MemoryEventSink(
        projection=FilteredEventProjection(
            minimum_severity=EventSeverity.WARNING,
            allowed_names=frozenset({'data.downloaded'}),
            include_attributes=False,
            include_error_traceback=False,
        )
    )
    event = ObservabilityEvent(
        name='data.downloaded',
        category=EventCategory.DATA,
        attributes={'query': 'not-for-cloud'},
    )

    sink.emit(event, _settings(), DataSanitizer())

    assert len(sink.events) == 1
    assert 'attributes' not in sink.events[0]


def test_memory_sink_is_bounded_and_returns_copies() -> None:
    sink = MemoryEventSink(max_events=2)
    for index in range(3):
        sink.emit(
            ObservabilityEvent(
                name=f'diagnostic.{index}',
                category=EventCategory.DIAGNOSTIC,
            ),
            _settings(),
            DataSanitizer(),
        )

    events = sink.events
    events[0]['name'] = 'changed'

    assert [event['name'] for event in sink.events] == ['diagnostic.1', 'diagnostic.2']


def test_operational_projection_flattens_identity_and_removes_technical_metadata() -> None:
    from atlanticus.observability import OperationalEventProjection

    event = ObservabilityEvent(
        name='runtime.execution.summary',
        category=EventCategory.LIFECYCLE,
        audience=EventAudience.OPERATIONS,
        metrics={'cpu_peak_percent': 18.2},
        attributes={
            'new_data': True,
            'receive_mode': 'peek_lock',
            'credential_scope': 'secret',
        },
    )
    payload = event.to_dict(settings=_settings(), sanitizer=DataSanitizer())

    projected = OperationalEventProjection().project(event, payload)

    assert projected is not None
    assert projected['application'] == 'payments'
    assert projected['environment'] == 'local'
    assert projected['service'] == 'daily-job'
    assert projected['event'] == 'execution.completed'
    assert projected['cpu_peak_percent'] == 18.2
    assert projected['new_data'] is True
    assert 'receive_mode' not in projected
    assert 'credential_scope' not in projected
    assert 'context' not in projected


def test_logger_exception_exposes_root_cause_without_traceback_in_operational_projection() -> None:
    from atlanticus.observability import (
        OperationalEventProjection,
        close_observability,
        configure_observability,
        get_observability_logger,
    )

    sink = MemoryEventSink(projection=OperationalEventProjection())
    configure_observability(settings=_settings(), sink=sink)
    try:
        try:
            raise ValueError('root cause')
        except ValueError as cause:
            try:
                raise RuntimeError('outer failure') from cause
            except RuntimeError as error:
                get_observability_logger('test').exception(
                    'Controlled failure',
                    error,
                    event_name='validation.failed',
                )
    finally:
        close_observability()

    assert len(sink.events) == 1
    assert sink.events[0]['error_type'] == 'RuntimeError'
    assert sink.events[0]['error_message'] == 'RuntimeError raised'
    assert sink.events[0]['cause_type'] == 'ValueError'
    assert sink.events[0]['cause_message'] == 'ValueError raised'
    assert sink.events[0]['diagnostic_available'] is True
    assert 'traceback' not in sink.events[0]


def test_console_text_renders_skipped_iteration_and_interrupted_execution() -> None:
    from atlanticus.observability import ConsoleTextSink, OperationalEventProjection

    stdout = StringIO()
    sink = ConsoleTextSink(projection=OperationalEventProjection(), stdout=stdout)
    sanitizer = DataSanitizer()
    iteration = ObservabilityEvent(
        name='runtime.iteration.summary',
        category=EventCategory.ITERATION,
        audience=EventAudience.OPERATIONS,
        attributes={
            'outcome': 'skipped',
            'reason': 'no_new_data',
            'messages': 2,
            'interpolated': 2,
            'new_data': False,
        },
    )
    cancelled = ObservabilityEvent(
        name='execution.cancelled',
        category=EventCategory.LIFECYCLE,
        audience=EventAudience.OPERATIONS,
    )

    sink.emit(iteration, _settings(), sanitizer)
    sink.emit(cancelled, _settings(), sanitizer)

    lines = stdout.getvalue().splitlines()
    assert 'daily-job iteration skipped' in lines[0]
    assert 'outcome=skipped' in lines[0]
    assert 'reason=no_new_data' in lines[0]
    assert 'messages=2' in lines[0]
    assert 'interpolated=2' in lines[0]
    assert 'new_data=false' in lines[0]
    assert 'daily-job interrupted' in lines[1]


def test_dispatch_source_completion_is_part_of_operational_projection() -> None:
    from atlanticus.observability import OperationalEventProjection

    event = ObservabilityEvent(
        name='dispatch.source.completed',
        category=EventCategory.DIAGNOSTIC,
        attributes={
            'source': 'std_shift_dumps',
            'rows': 42,
            'partitions_changed': 2,
            'new_data': True,
        },
    )
    payload = event.to_dict(settings=_settings(), sanitizer=DataSanitizer())

    projected = OperationalEventProjection().project(event, payload)

    assert projected is not None
    assert projected['event'] == 'dispatch.source.completed'
    assert projected['source'] == 'std_shift_dumps'
    assert projected['rows'] == 42
    assert projected['partitions_changed'] == 2
    assert projected['new_data'] is True
