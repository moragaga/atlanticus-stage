"""Configuración explícita y emisión fail-safe a nivel de proceso."""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

from atlanticus.kernel import DataSanitizer
from atlanticus.observability.context import get_execution_context
from atlanticus.observability.models import (
    ExecutionContext,
    ObservabilityEvent,
    ObservabilitySettings,
)
from atlanticus.observability.sinks import EventSink, NoopEventSink
from atlanticus.observability.tracing import NoopTraceBridge, SpanError, TraceBridge


@dataclass(slots=True)
class Observability:
    """Combina identidad, sanitización y un sink reemplazable."""

    settings: ObservabilitySettings
    sink: EventSink
    sanitizer: DataSanitizer
    trace_bridge: TraceBridge

    def __post_init__(self) -> None:
        if not isinstance(self.settings, ObservabilitySettings):
            raise TypeError('settings must be ObservabilitySettings')
        if not isinstance(self.sink, EventSink):
            raise TypeError('sink must be an EventSink')
        if not isinstance(self.sanitizer, DataSanitizer):
            raise TypeError('sanitizer must be a DataSanitizer')
        _validate_trace_bridge(self.trace_bridge)

    def emit(self, event: ObservabilityEvent) -> None:
        """Captura el contexto activo antes de entregar el evento."""

        if not isinstance(event, ObservabilityEvent):
            raise TypeError('event must be an ObservabilityEvent')
        context = _merge_context(self.settings, event.context or get_execution_context())
        self.sink.emit(replace(event, context=context), self.settings, self.sanitizer)

    def close(self) -> None:
        first_error: Exception | None = None
        try:
            self.sink.close()
        except Exception as error:
            first_error = error
        try:
            self.trace_bridge.close()
        except Exception as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error


_state_lock = threading.RLock()
_default_observability: Observability | None = None


def configure_observability(
    *,
    settings: ObservabilitySettings,
    sink: EventSink | None = None,
    sanitizer: DataSanitizer | None = None,
    trace_bridge: TraceBridge | None = None,
) -> Observability:
    """Configura observabilidad sin leer ambiente ni archivos automáticamente."""

    if not isinstance(settings, ObservabilitySettings):
        raise TypeError('settings must be ObservabilitySettings')
    if sink is not None and not isinstance(sink, EventSink):
        raise TypeError('sink must be an EventSink')
    if sanitizer is not None and not isinstance(sanitizer, DataSanitizer):
        raise TypeError('sanitizer must be a DataSanitizer')
    if trace_bridge is not None:
        _validate_trace_bridge(trace_bridge)

    global _default_observability
    configured = Observability(
        settings=settings,
        sink=sink or NoopEventSink(),
        sanitizer=sanitizer or DataSanitizer(),
        trace_bridge=trace_bridge or NoopTraceBridge(),
    )
    with _state_lock:
        previous = _default_observability
        _default_observability = configured
    if previous is not None:
        try:
            previous.close()
        except Exception:
            pass
    return configured


def get_observability() -> Observability | None:
    """Retorna la configuración actual o ``None`` antes del bootstrap."""

    with _state_lock:
        return _default_observability


def emit_event(event: ObservabilityEvent) -> bool:
    """Emite sin permitir que un fallo de telemetría interrumpa el proceso."""

    observability = get_observability()
    if observability is None:
        return False
    try:
        observability.emit(event)
    except Exception:
        return False
    return True


@contextmanager
def trace_span(
    name: str,
    *,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[None]:
    """Abre un span opcional y garantiza que el bridge nunca cambie el negocio."""

    observability = get_observability()
    handle = None
    if observability is not None:
        try:
            context = _merge_context(observability.settings, get_execution_context())
            sanitized = observability.sanitizer.sanitize(dict(attributes or {}))
            safe_attributes = sanitized if isinstance(sanitized, dict) else {}
            handle = observability.trace_bridge.start_span(
                name,
                context=context,
                attributes=safe_attributes,
            )
        except Exception:
            handle = None
    try:
        yield
    except BaseException as error:
        if handle is not None:
            try:
                handle.end(
                    SpanError(
                        error_type=type(error).__name__,
                        message=f'{type(error).__name__} raised',
                    )
                )
            except Exception:
                pass
        raise
    else:
        if handle is not None:
            try:
                handle.end()
            except Exception:
                pass


def close_observability() -> bool:
    """Cierra el estado sin permitir que un fallo de telemetría afecte al proceso."""

    global _default_observability
    with _state_lock:
        current = _default_observability
        _default_observability = None
    if current is None:
        return True
    try:
        current.close()
    except Exception:
        return False
    return True


def _merge_context(
    settings: ObservabilitySettings,
    context: ExecutionContext,
) -> ExecutionContext:
    base = settings.base_context()
    values = context.to_dict()
    if not values:
        return base
    return replace(
        base,
        **{
            key: value
            for key, value in values.items()
            if key not in {'application', 'service', 'component', 'environment', 'instance_id'}
            or value is not None
        },
    )


def _validate_trace_bridge(trace_bridge: Any) -> None:
    if not callable(getattr(trace_bridge, 'start_span', None)) or not callable(
        getattr(trace_bridge, 'close', None)
    ):
        raise TypeError('trace_bridge must implement start_span() and close()')
