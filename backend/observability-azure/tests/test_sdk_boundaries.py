from __future__ import annotations

from atlanticus.observability import ExecutionContext, SpanError
from atlanticus.observability_azure import AzureMonitorTraceBridge, OpenTelemetryLogBackend


class _Resource:
    values = None

    @classmethod
    def create(cls, values):
        cls.values = values
        return values


class _Exporter:
    instances = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.__class__.instances.append(self)


class _Processor:
    instances = []

    def __init__(self, exporter, **kwargs) -> None:
        self.exporter = exporter
        self.kwargs = kwargs
        self.__class__.instances.append(self)


class _LoggerProvider:
    instance = None

    def __init__(self, *, resource) -> None:
        self.resource = resource
        self.processors = []
        self.flushed = []
        self.shutdown_count = 0
        self.__class__.instance = self

    def add_log_record_processor(self, processor) -> None:
        self.processors.append(processor)

    def force_flush(self, *, timeout_millis) -> None:
        self.flushed.append(timeout_millis)

    def shutdown(self) -> None:
        self.shutdown_count += 1


class _LoggingHandler:
    def __init__(self, *, logger_provider) -> None:
        self.logger_provider = logger_provider


class _Span:
    def __init__(self, name, **kwargs) -> None:
        self.name = name
        self.kwargs = kwargs
        self.events = []
        self.statuses = []
        self.end_times = []

    def add_event(self, name, attributes) -> None:
        self.events.append((name, attributes))

    def set_status(self, status, description) -> None:
        self.statuses.append((status, description))

    def end(self, *, end_time) -> None:
        self.end_times.append(end_time)


class _Tracer:
    def __init__(self) -> None:
        self.spans = []

    def start_span(self, name, **kwargs):
        span = _Span(name, **kwargs)
        self.spans.append(span)
        return span


class _TracerProvider:
    instance = None

    def __init__(self, *, resource, sampler) -> None:
        self.resource = resource
        self.sampler = sampler
        self.processors = []
        self.tracer = _Tracer()
        self.flushed = []
        self.shutdown_count = 0
        self.__class__.instance = self

    def add_span_processor(self, processor) -> None:
        self.processors.append(processor)

    def get_tracer(self, name):
        self.tracer_name = name
        return self.tracer

    def force_flush(self, *, timeout_millis) -> None:
        self.flushed.append(timeout_millis)

    def shutdown(self) -> None:
        self.shutdown_count += 1


def _reset_doubles() -> None:
    _Exporter.instances = []
    _Processor.instances = []
    _LoggerProvider.instance = None
    _TracerProvider.instance = None


def test_log_backend_configures_only_bounded_log_export(monkeypatch) -> None:
    _reset_doubles()
    monkeypatch.setattr(
        'azure.monitor.opentelemetry.exporter.AzureMonitorLogExporter',
        _Exporter,
    )
    monkeypatch.setattr('opentelemetry.sdk.resources.Resource', _Resource)
    monkeypatch.setattr('opentelemetry.sdk._logs.LoggerProvider', _LoggerProvider)
    monkeypatch.setattr('opentelemetry.sdk._logs.LoggingHandler', _LoggingHandler)
    monkeypatch.setattr(
        'opentelemetry.sdk._logs.export.BatchLogRecordProcessor',
        _Processor,
    )

    backend = OpenTelemetryLogBackend(
        connection_string='InstrumentationKey=fake',
        application='ada',
        service='dispatch-job',
        flush_timeout_seconds=3,
    )

    assert _Resource.values == {'service.namespace': 'ada', 'service.name': 'dispatch-job'}
    assert _Exporter.instances[0].kwargs == {
        'connection_string': 'InstrumentationKey=fake',
        'disable_offline_storage': True,
    }
    assert _Processor.instances[0].kwargs == {'export_timeout_millis': 3000}
    assert _LoggerProvider.instance.processors == [_Processor.instances[0]]

    backend.close()
    assert _LoggerProvider.instance.flushed == [3000]
    assert _LoggerProvider.instance.shutdown_count == 1


def test_trace_backend_exports_only_selected_context(monkeypatch) -> None:
    _reset_doubles()
    monkeypatch.setattr(
        'azure.monitor.opentelemetry.exporter.AzureMonitorTraceExporter',
        _Exporter,
    )
    monkeypatch.setattr('opentelemetry.sdk.resources.Resource', _Resource)
    monkeypatch.setattr('opentelemetry.sdk.trace.TracerProvider', _TracerProvider)
    monkeypatch.setattr('opentelemetry.sdk.trace.export.BatchSpanProcessor', _Processor)

    bridge = AzureMonitorTraceBridge(
        connection_string='InstrumentationKey=fake',
        application='ada',
        service='dispatch-job',
        flush_timeout_seconds=3,
    )
    handle = bridge.start_span(
        'blob.download',
        context=ExecutionContext(
            application='ada',
            environment='dev',
            service='dispatch-job',
            run_id='run-1',
        ),
        attributes={
            'component': 'atlanticus.connectivity.blob',
            'source': 'primary',
            'credential_scope': 'must-not-be-exported',
        },
    )
    handle.end(SpanError(error_type='TimeoutError', message='TimeoutError raised'))

    span = _TracerProvider.instance.tracer.spans[0]
    assert span.kwargs['attributes']['atlanticus.component'] == 'atlanticus.connectivity.blob'
    assert span.kwargs['attributes']['atlanticus.source'] == 'primary'
    assert 'credential_scope' not in str(span.kwargs)
    assert span.events == [
        (
            'exception',
            {
                'exception.type': 'TimeoutError',
                'exception.message': 'TimeoutError raised',
            },
        )
    ]
    assert _Exporter.instances[0].kwargs['disable_offline_storage'] is True
    assert _Processor.instances[0].kwargs == {'export_timeout_millis': 3000}

    bridge.close()
    bridge.close()
    assert _TracerProvider.instance.flushed == [3000]
    assert _TracerProvider.instance.shutdown_count == 1
