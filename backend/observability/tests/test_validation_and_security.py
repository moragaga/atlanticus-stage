from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlanticus.kernel import DataSanitizer, Environment
from atlanticus.observability import (
    CompositeEventSink,
    DailyTraceSink,
    ErrorInfo,
    EventCategory,
    ExecutionContext,
    FilteredEventProjection,
    MemoryEventSink,
    ObservabilityEvent,
    ObservabilitySettings,
    ResultSummary,
    close_observability,
    configure_observability,
    get_observability_logger,
    runtime_guard,
    trace_execution,
)
from atlanticus.observability.tracing import NoopTraceBridge, SpanError


class _SpanHandle:
    def __init__(self, errors: list[SpanError]) -> None:
        self._errors = errors

    def end(self, error: SpanError | None = None) -> None:
        if error is not None:
            self._errors.append(error)


class _TraceBridge(NoopTraceBridge):
    def __init__(self) -> None:
        self.errors: list[SpanError] = []

    def start_span(self, name, *, context, attributes):
        return _SpanHandle(self.errors)


def _settings() -> ObservabilitySettings:
    return ObservabilitySettings.build(
        application='app',
        service='job',
        environment='local',
        instance_id='tests',
    )


def test_exception_factory_preserves_types_and_location_without_messages() -> None:
    secret = 'https://storage.example/file?sig=secret-signature'

    try:
        try:
            raise ValueError(secret)
        except ValueError as cause:
            raise RuntimeError(secret) from cause
    except RuntimeError as error:
        info = ErrorInfo.from_exception(error)

    serialized = json.dumps(info.to_dict(DataSanitizer()))
    assert secret not in serialized
    assert info.error_type == 'RuntimeError'
    assert info.message == 'RuntimeError raised'
    assert info.cause_type == 'ValueError'
    assert info.cause_message == 'ValueError raised'
    assert info.traceback is not None
    assert 'test_exception_factory_preserves_types_and_location_without_messages' in info.traceback


def test_automatic_error_paths_never_copy_exception_messages() -> None:
    secret = 'Endpoint=sb://example/;SharedAccessKey=secret'
    sink = MemoryEventSink()
    bridge = _TraceBridge()
    configure_observability(settings=_settings(), sink=sink, trace_bridge=bridge)
    try:
        try:
            raise RuntimeError(secret)
        except RuntimeError as error:
            get_observability_logger('test').exception('Controlled failure', error)

        with pytest.raises(RuntimeError):
            with trace_execution():
                raise RuntimeError(secret)

        @runtime_guard(operation='dependency.read', component='dependency')
        def guarded() -> None:
            raise RuntimeError(secret)

        with pytest.raises(RuntimeError):
            guarded()
    finally:
        close_observability()

    assert secret not in json.dumps(sink.events)
    assert bridge.errors
    assert all(error.message == 'RuntimeError raised' for error in bridge.errors)


def test_event_captures_a_deeply_immutable_snapshot() -> None:
    source = {'items': [{'value': 1}]}
    event = ObservabilityEvent(
        name='snapshot.created',
        category=EventCategory.DATA,
        attributes=source,
    )

    source['items'][0]['value'] = 2
    source['items'].append({'value': 3})

    payload = event.to_dict(settings=_settings(), sanitizer=DataSanitizer())
    assert payload['attributes'] == {'items': [{'value': 1}]}
    with pytest.raises(TypeError):
        event.attributes['new'] = True
    with pytest.raises(TypeError):
        event.attributes['items'][0]['value'] = 2


def test_result_summary_captures_a_deeply_immutable_snapshot() -> None:
    values = {'items': ['one']}
    summary = ResultSummary(attributes=values)

    values['items'].append('two')

    assert summary.attributes['items'] == ('one',)
    with pytest.raises(AttributeError):
        summary.attributes['items'].append('three')


def test_settings_reject_invalid_direct_environment_and_path() -> None:
    with pytest.raises(TypeError, match='environment must be an Environment'):
        ObservabilitySettings(
            application='app',
            service='job',
            component='runtime',
            environment='local',
        )

    with pytest.raises(TypeError, match='volume_path must be a Path'):
        ObservabilitySettings(
            application='app',
            service='job',
            component='runtime',
            environment=Environment.from_value('local'),
            volume_path='/tmp',
        )


@pytest.mark.parametrize(
    ('factory', 'message'),
    [
        (
            lambda: ObservabilityEvent(name='event', category='data'),
            'category must be an EventCategory',
        ),
        (
            lambda: ObservabilityEvent(
                name='event',
                category=EventCategory.DATA,
                context='invalid',
            ),
            'context must be an ExecutionContext',
        ),
        (lambda: ExecutionContext(iteration=0), 'iteration must be greater than zero'),
        (
            lambda: ErrorInfo(error_type='RuntimeError', message='raised', retryable='yes'),
            'retryable must be a bool',
        ),
    ],
)
def test_public_models_reject_invalid_contracts(factory, message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        factory()


def test_projections_and_sinks_reject_invalid_contracts(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match='allowed_names must be a frozenset'):
        FilteredEventProjection(allowed_names={'event'})
    with pytest.raises(TypeError, match='minimum_severity must be an EventSeverity'):
        FilteredEventProjection(minimum_severity='warning')
    with pytest.raises(TypeError, match='sinks must contain only EventSink instances'):
        CompositeEventSink((object(),))
    with pytest.raises(TypeError, match='durable_minimum_severity must be an EventSeverity'):
        DailyTraceSink(tmp_path, durable_minimum_severity='warning')


def test_explicit_error_mapper_can_supply_a_controlled_message() -> None:
    sink = MemoryEventSink()
    configure_observability(settings=_settings(), sink=sink)
    try:

        @runtime_guard(
            operation='dependency.read',
            component='dependency',
            error_mapper=lambda error: ErrorInfo(
                error_type=type(error).__name__,
                message='Dependency read failed',
            ),
        )
        def guarded() -> None:
            raise RuntimeError('untrusted SDK message')

        with pytest.raises(RuntimeError):
            guarded()
    finally:
        close_observability()

    assert sink.events[-1]['error']['message'] == 'Dependency read failed'


def test_set_execution_context_rejects_invalid_type() -> None:
    from atlanticus.observability import set_execution_context

    with pytest.raises(TypeError, match='context must be an ExecutionContext'):
        set_execution_context('invalid')


def test_unknown_attribute_object_is_not_deep_copied() -> None:
    class _Opaque:
        def __deepcopy__(self, memo):
            raise AssertionError('__deepcopy__ must not be called')

    event = ObservabilityEvent(
        name='diagnostic.opaque',
        category=EventCategory.DIAGNOSTIC,
        attributes={'opaque': _Opaque()},
    )

    payload = event.to_dict(settings=_settings(), sanitizer=DataSanitizer())

    assert payload['attributes']['opaque'] == {
        'type': '_Opaque',
        'summary': 'unsupported_object',
    }
