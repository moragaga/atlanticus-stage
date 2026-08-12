"""Bridges de tracing acotado para preview y Azure Monitor."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from atlanticus.observability import ExecutionContext, ObservabilitySettings, SpanError, SpanHandle
from atlanticus.observability_azure.preview import AzurePreviewWriter

# Solo excluimos spans de lifecycle naturalmente largos; ninguna dependencia concreta se hardcodea aquí.
_IGNORED_SUCCESS_SPANS = frozenset({'execution', 'iteration'})
_SLOW_SPAN_THRESHOLD_MS = 2_000
_MAX_TRACE_TEXT_LENGTH = 256
_TRACE_ATTRIBUTE_NAMES = ('component', 'source')


def _should_emit_span(name: str, duration_ms: float, error: SpanError | None) -> bool:
    if error is not None:
        return True
    return name not in _IGNORED_SUCCESS_SPANS and duration_ms >= _SLOW_SPAN_THRESHOLD_MS


# La identidad estática proviene del bridge; el contexto aporta únicamente datos dinámicos de la ejecución.
def _compact_span_values(
    *,
    name: str,
    application: str,
    environment: str,
    service: str,
    context: ExecutionContext,
    attributes: Mapping[str, str],
    duration_ms: float,
    error: SpanError | None,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        'application': application,
        'environment': environment,
        'service': service,
        'run_id': context.run_id,
        'iteration': context.iteration,
        'span': name,
        'component': attributes.get('component'),
        'source': attributes.get('source'),
        'duration_ms': duration_ms,
        'status': 'error' if error is not None else 'slow',
    }
    if error is not None:
        values['error_type'] = error.error_type
        values['error_message'] = error.message
    return {key: value for key, value in values.items() if value is not None}


def _validate_span_error(error: SpanError | None) -> None:
    if error is not None and not isinstance(error, SpanError):
        raise TypeError('error must be a SpanError or None')


def _snapshot_span(
    name: str,
    context: ExecutionContext,
    attributes: Mapping[str, Any],
) -> tuple[str, ExecutionContext, dict[str, str]]:
    if not isinstance(name, str) or not name.strip():
        raise ValueError('name must be a non-empty string')
    if not isinstance(context, ExecutionContext):
        raise TypeError('context must be an ExecutionContext')
    if not isinstance(attributes, Mapping):
        raise TypeError('attributes must be a mapping')
    selected: dict[str, str] = {}
    for key in _TRACE_ATTRIBUTE_NAMES:
        value = attributes.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise TypeError(f'{key} must be a string')
        normalized = value.strip()
        if not normalized:
            raise ValueError(f'{key} must not be empty')
        if len(normalized) > _MAX_TRACE_TEXT_LENGTH:
            normalized = f'{normalized[: _MAX_TRACE_TEXT_LENGTH - 1]}…'
        selected[key] = normalized
    return name.strip(), context, selected


class _PreviewSpanHandle:
    def __init__(
        self,
        *,
        name: str,
        context: ExecutionContext,
        attributes: Mapping[str, str],
        settings: ObservabilitySettings,
        writer: AzurePreviewWriter,
    ) -> None:
        self._name = name
        self._context = context
        self._attributes = dict(attributes)
        self._settings = settings
        self._writer = writer
        self._started_monotonic = time.monotonic()
        self._ended = False
        self._lock = Lock()

    def end(self, error: SpanError | None = None) -> None:
        _validate_span_error(error)
        with self._lock:
            if self._ended:
                return
            self._ended = True
        ended_at = datetime.now(UTC)
        duration_ms = round((time.monotonic() - self._started_monotonic) * 1000, 3)
        if not _should_emit_span(self._name, duration_ms, error):
            return
        payload = {
            'time': ended_at.isoformat(),
            **_compact_span_values(
                name=self._name,
                application=self._settings.application,
                environment=str(self._settings.environment),
                service=self._settings.service,
                context=self._context,
                attributes=self._attributes,
                duration_ms=duration_ms,
                error=error,
            ),
        }
        self._writer.append(
            payload,
            settings=self._settings,
            event_day=ended_at.date(),
            durable=error is not None,
            file_name='azure-diagnostic-spans.jsonl',
        )


class AzurePreviewTraceBridge:
    """Conserva sólo errores y spans lentos accionables en preview."""

    def __init__(
        self,
        *,
        settings: ObservabilitySettings,
        writer: AzurePreviewWriter,
    ) -> None:
        if not isinstance(settings, ObservabilitySettings):
            raise TypeError('settings must be an ObservabilitySettings')
        if not isinstance(writer, AzurePreviewWriter):
            raise TypeError('writer must be an AzurePreviewWriter')
        self._settings = settings
        self._writer = writer
        self._closed = False
        self._lock = Lock()

    def start_span(
        self,
        name: str,
        *,
        context: ExecutionContext,
        attributes: dict[str, Any],
    ) -> SpanHandle:
        with self._lock:
            if self._closed:
                raise RuntimeError('Azure preview trace bridge is closed')
        resolved_name, resolved_context, resolved_attributes = _snapshot_span(
            name,
            context,
            attributes,
        )
        return _PreviewSpanHandle(
            name=resolved_name,
            context=resolved_context,
            attributes=resolved_attributes,
            settings=self._settings,
            writer=self._writer,
        )

    def close(self) -> None:
        with self._lock:
            self._closed = True


class _AzureMonitorSpanHandle:
    def __init__(
        self,
        *,
        tracer: Any,
        status_type: Any,
        name: str,
        application: str,
        environment: str,
        service: str,
        context: ExecutionContext,
        attributes: Mapping[str, str],
    ) -> None:
        self._tracer = tracer
        self._status_type = status_type
        self._name = name
        self._application = application
        self._environment = environment
        self._service = service
        self._context = context
        self._attributes = dict(attributes)
        self._started_monotonic = time.monotonic()
        self._started_time_ns = time.time_ns()
        self._ended = False
        self._lock = Lock()

    def end(self, error: SpanError | None = None) -> None:
        _validate_span_error(error)
        with self._lock:
            if self._ended:
                return
            self._ended = True
        ended_time_ns = time.time_ns()
        duration_ms = round((time.monotonic() - self._started_monotonic) * 1000, 3)
        if not _should_emit_span(self._name, duration_ms, error):
            return
        values = _compact_span_values(
            name=self._name,
            application=self._application,
            environment=self._environment,
            service=self._service,
            context=self._context,
            attributes=self._attributes,
            duration_ms=duration_ms,
            error=error,
        )
        span = self._tracer.start_span(
            self._name,
            attributes={
                f'atlanticus.{key}': value
                for key, value in values.items()
                if key not in {'span', 'error_message'}
            },
            start_time=self._started_time_ns,
        )
        if error is not None:
            span.add_event(
                'exception',
                {
                    'exception.type': error.error_type,
                    'exception.message': error.message,
                },
            )
            span.set_status(self._status_type.ERROR, error.message)
        span.end(end_time=ended_time_ns)


# El bridge conserva identidad autoritativa para que un contexto accidentalmente inconsistente no falsee Azure.
class AzureMonitorTraceBridge:
    """Exporta sólo errores y spans lentos accionables en perfil diagnóstico."""

    def __init__(
        self,
        *,
        connection_string: str,
        application: str,
        service: str,
        environment: str,
        flush_timeout_seconds: float,
    ) -> None:
        _require_text(connection_string, 'connection_string')
        _require_text(application, 'application')
        _require_text(service, 'service')
        _require_text(environment, 'environment')
        _validate_timeout(flush_timeout_seconds)

        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON
        from opentelemetry.trace import StatusCode

        self._flush_timeout_millis = int(flush_timeout_seconds * 1000)
        self._application = application
        self._environment = environment
        self._service = service
        resource = Resource.create(
            {
                'service.namespace': application,
                'service.name': service,
            }
        )
        self._provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
        exporter = AzureMonitorTraceExporter(
            connection_string=connection_string,
            disable_offline_storage=True,
        )
        self._provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                export_timeout_millis=self._flush_timeout_millis,
            )
        )
        self._tracer = self._provider.get_tracer('atlanticus.observability')
        self._status_type = StatusCode
        self._closed = False
        self._lock = Lock()

    def start_span(
        self,
        name: str,
        *,
        context: ExecutionContext,
        attributes: dict[str, Any],
    ) -> SpanHandle:
        with self._lock:
            if self._closed:
                raise RuntimeError('Azure monitor trace bridge is closed')
        resolved_name, resolved_context, resolved_attributes = _snapshot_span(
            name,
            context,
            attributes,
        )
        return _AzureMonitorSpanHandle(
            tracer=self._tracer,
            status_type=self._status_type,
            name=resolved_name,
            application=self._application,
            environment=self._environment,
            service=self._service,
            context=resolved_context,
            attributes=resolved_attributes,
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._provider.force_flush(timeout_millis=self._flush_timeout_millis)
        finally:
            self._provider.shutdown()


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a non-empty string')
    return value


def _validate_timeout(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError('flush_timeout_seconds must be an int or float')
    if not math.isfinite(value) or value <= 0:
        raise ValueError('flush_timeout_seconds must be greater than zero')
