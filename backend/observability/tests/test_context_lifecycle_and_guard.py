from __future__ import annotations

import asyncio

import pytest

from atlanticus.observability import (
    EventAudience,
    MemoryEventSink,
    NoopTraceBridge,
    ObservabilitySettings,
    ResultSummary,
    close_observability,
    configure_observability,
    get_execution_context,
    runtime_guard,
    trace_execution,
    trace_iteration,
)
from atlanticus.observability.tracing import SpanError


class _SpanHandle:
    def __init__(self, record: dict) -> None:
        self._record = record

    def end(self, error: SpanError | None = None) -> None:
        self._record['error'] = error


class _TraceBridge(NoopTraceBridge):
    def __init__(self) -> None:
        self.spans: list[dict] = []

    def start_span(self, name, *, context, attributes):
        record = {
            'name': name,
            'context': context.to_dict(),
            'attributes': attributes,
            'error': None,
        }
        self.spans.append(record)
        return _SpanHandle(record)


class _FailingCloseTraceBridge(NoopTraceBridge):
    def close(self) -> None:
        raise RuntimeError('exporter unavailable')


@pytest.fixture
def memory_sink() -> MemoryEventSink:
    sink = MemoryEventSink()
    configure_observability(
        settings=ObservabilitySettings.build(
            application='app',
            service='job',
            environment='local',
            instance_id='tests',
        ),
        sink=sink,
    )
    yield sink
    close_observability()


def test_lifecycle_links_execution_and_iteration(memory_sink: MemoryEventSink) -> None:
    with trace_execution(run_id='run-1', correlation_id='correlation-1'):
        with trace_iteration(3):
            context = get_execution_context()
            assert context.run_id == 'run-1'
            assert context.iteration == 3

    events = memory_sink.events
    assert [event['name'] for event in events] == [
        'execution.started',
        'iteration.started',
        'iteration.finished',
        'execution.finished',
    ]
    assert events[2]['context']['run_id'] == 'run-1'
    assert events[2]['context']['iteration'] == 3
    assert events[2]['duration_ms'] >= 0
    assert events[2]['audience'] == EventAudience.OPERATIONS


def test_trace_bridge_receives_merged_context_and_nested_spans() -> None:
    bridge = _TraceBridge()
    configure_observability(
        settings=ObservabilitySettings.build(
            application='app',
            service='job',
            environment='local',
            instance_id='tests',
        ),
        sink=MemoryEventSink(),
        trace_bridge=bridge,
    )
    try:
        with trace_execution(run_id='run-1', correlation_id='correlation-1'):
            with trace_iteration(2):
                pass
    finally:
        close_observability()

    assert [span['name'] for span in bridge.spans] == ['execution', 'iteration']
    assert bridge.spans[0]['context']['application'] == 'app'
    assert bridge.spans[0]['context']['service'] == 'job'
    assert bridge.spans[1]['context']['run_id'] == 'run-1'
    assert bridge.spans[1]['context']['iteration'] == 2


def test_global_close_is_fail_safe() -> None:
    configure_observability(
        settings=ObservabilitySettings.build(
            application='app',
            service='job',
            environment='local',
        ),
        sink=MemoryEventSink(),
        trace_bridge=_FailingCloseTraceBridge(),
    )

    assert close_observability() is False
    assert close_observability() is True


def test_runtime_guard_summarizes_result_without_capturing_it(
    memory_sink: MemoryEventSink,
) -> None:
    @runtime_guard(
        operation='cosmos.query',
        component='cosmos',
        target_alias='catalog-read',
        parameter_mapper=lambda args, kwargs: {'partition_supplied': bool(args[0])},
        result_mapper=lambda result: ResultSummary(metrics={'record_count': len(result)}),
    )
    def read(partition: str) -> list[dict[str, int]]:
        return [{'id': 1}, {'id': 2}]

    result = read('north')

    assert result == [{'id': 1}, {'id': 2}]
    finished = memory_sink.events[-1]
    assert finished['name'] == 'dependency.finished'
    assert finished['metrics']['record_count'] == 2
    assert finished['context']['target_alias'] == 'catalog-read'
    assert 'result' not in finished.get('attributes', {})


def test_runtime_guard_preserves_original_exception(memory_sink: MemoryEventSink) -> None:
    expected = RuntimeError('cosmos unavailable')

    @runtime_guard(operation='cosmos.write', component='cosmos')
    def write() -> None:
        raise expected

    with pytest.raises(RuntimeError) as captured:
        write()

    assert captured.value is expected
    assert memory_sink.events[-1]['name'] == 'dependency.failed'
    assert memory_sink.events[-1]['error']['type'] == 'RuntimeError'


def test_runtime_guard_supports_async_functions(memory_sink: MemoryEventSink) -> None:
    @runtime_guard(operation='service_bus.send', component='service-bus')
    async def send() -> str:
        await asyncio.sleep(0)
        return 'message-id'

    assert asyncio.run(send()) == 'message-id'
    assert memory_sink.events[-1]['name'] == 'dependency.finished'


def test_mapper_failure_never_changes_business_result(memory_sink: MemoryEventSink) -> None:
    def invalid_mapper(result: object) -> ResultSummary:
        raise ValueError('bad mapper')

    @runtime_guard(
        operation='redis.get',
        component='redis',
        result_mapper=invalid_mapper,
    )
    def read() -> str:
        return 'business-value'

    assert read() == 'business-value'
    assert [event['name'] for event in memory_sink.events][-2:] == [
        'observability.mapper.failed',
        'dependency.finished',
    ]
