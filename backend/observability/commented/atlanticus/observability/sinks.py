"""Proyecciones y destinos independientes para eventos estructurados."""

from __future__ import annotations

import json
import sys
import threading
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, TextIO

from atlanticus.kernel import DataSanitizer
from atlanticus.observability.models import (
    EventCategory,
    EventSeverity,
    ObservabilityEvent,
    ObservabilitySettings,
)

_SEVERITY_ORDER = {
    EventSeverity.DEBUG: 10,
    EventSeverity.INFO: 20,
    EventSeverity.WARNING: 30,
    EventSeverity.ERROR: 40,
    EventSeverity.CRITICAL: 50,
}


class EventProjection(ABC):
    """Decide si un evento se entrega y qué campos conserva el sink."""

    @abstractmethod
    def project(
        self,
        event: ObservabilityEvent,
        payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Retorna un payload nuevo o ``None`` para descartar el evento."""


class FullEventProjection(EventProjection):
    """Conserva el evento materializado completo."""

    def project(
        self,
        event: ObservabilityEvent,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        # Se entrega una copia independiente para que el destino no modifique el payload compartido.
        _validate_projection_input(event, payload)
        return deepcopy(dict(payload))


@dataclass(frozen=True, slots=True)
class FilteredEventProjection(EventProjection):
    """Proyección configurable para consola desplegada y adaptadores futuros."""

    minimum_severity: EventSeverity = EventSeverity.INFO
    allowed_categories: frozenset[EventCategory] = field(default_factory=frozenset)
    allowed_names: frozenset[str] = field(default_factory=frozenset)
    denied_names: frozenset[str] = field(default_factory=frozenset)
    include_attributes: bool = True
    include_metrics: bool = True
    include_error_traceback: bool = False
    include_resource_events: bool = True

    def __post_init__(self) -> None:
        # Los frozenset aseguran que la política no cambie mientras se están emitiendo eventos.
        if not isinstance(self.minimum_severity, EventSeverity):
            raise TypeError('minimum_severity must be an EventSeverity')
        _validate_frozenset(self.allowed_categories, EventCategory, 'allowed_categories')
        _validate_frozenset(self.allowed_names, str, 'allowed_names')
        _validate_frozenset(self.denied_names, str, 'denied_names')
        for name in (
            'include_attributes',
            'include_metrics',
            'include_error_traceback',
            'include_resource_events',
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f'{name} must be a bool')
        for name in self.allowed_names | self.denied_names:
            if not name.strip():
                raise ValueError('projection event names must not be empty')

    def project(
        self,
        event: ObservabilityEvent,
        payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        _validate_projection_input(event, payload)
        if event.name in self.denied_names:
            return None
        if event.category is EventCategory.RESOURCE and not self.include_resource_events:
            return None

        severity_allowed = _SEVERITY_ORDER[event.severity] >= _SEVERITY_ORDER[self.minimum_severity]
        specifically_allowed = event.name in self.allowed_names or bool(
            self.allowed_categories and event.category in self.allowed_categories
        )
        if not severity_allowed and not specifically_allowed:
            return None

        projected = deepcopy(dict(payload))
        if not self.include_attributes:
            projected.pop('attributes', None)
        if not self.include_metrics:
            projected.pop('metrics', None)
        if not self.include_error_traceback:
            error = projected.get('error')
            if isinstance(error, dict):
                error.pop('traceback', None)
        return projected


class EventSink(ABC):
    """Contrato mínimo implementado por todos los destinos."""

    @abstractmethod
    def emit(
        self,
        event: ObservabilityEvent,
        settings: ObservabilitySettings,
        sanitizer: DataSanitizer,
    ) -> None:
        """Entrega un evento ya validado."""

    def close(self) -> None:
        """Libera recursos cuando el sink los mantiene."""

        return None


class NoopEventSink(EventSink):
    """Sink inicial que permite importar el package antes de configurarlo."""

    def emit(
        self,
        event: ObservabilityEvent,
        settings: ObservabilitySettings,
        sanitizer: DataSanitizer,
    ) -> None:
        return None


class ConsoleJsonSink(EventSink):
    """Escribe una línea JSON por evento con proyección propia."""

    def __init__(
        self,
        *,
        projection: EventProjection | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        if projection is not None and not isinstance(projection, EventProjection):
            raise TypeError('projection must be an EventProjection')
        self._projection = projection or FullEventProjection()
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr
        self._lock = threading.Lock()

    def emit(
        self,
        event: ObservabilityEvent,
        settings: ObservabilitySettings,
        sanitizer: DataSanitizer,
    ) -> None:
        payload = event.to_dict(settings=settings, sanitizer=sanitizer)
        projected = self._projection.project(event, payload)
        if projected is None:
            return
        line = json.dumps(projected, ensure_ascii=False, sort_keys=True) + '\n'
        stream = (
            self._stderr
            if event.severity
            in {EventSeverity.WARNING, EventSeverity.ERROR, EventSeverity.CRITICAL}
            else self._stdout
        )
        with self._lock:
            stream.write(line)
            stream.flush()


class ConsoleTextSink(EventSink):
    """Renderiza en texto humano exactamente la proyección operacional entregada."""

    def __init__(
        self,
        *,
        projection: EventProjection,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        if not isinstance(projection, EventProjection):
            raise TypeError('projection must be an EventProjection')
        self._projection = projection
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr
        self._lock = threading.Lock()

    def emit(
        self,
        event: ObservabilityEvent,
        settings: ObservabilitySettings,
        sanitizer: DataSanitizer,
    ) -> None:
        payload = event.to_dict(settings=settings, sanitizer=sanitizer)
        projected = self._projection.project(event, payload)
        if projected is None:
            return
        stream = (
            self._stderr
            if event.severity
            in {EventSeverity.WARNING, EventSeverity.ERROR, EventSeverity.CRITICAL}
            else self._stdout
        )
        line = self._format(projected) + '\n'
        with self._lock:
            stream.write(line)
            stream.flush()

    @staticmethod
    def _format(payload: Mapping[str, Any]) -> str:
        time_value = str(payload.get('time', ''))
        clock = time_value[11:19] if len(time_value) >= 19 else time_value
        level = str(payload.get('level', 'info')).upper()
        service = str(payload.get('service', 'unknown'))
        event = str(payload.get('event', 'event'))
        message = payload.get('message')
        label = str(message) if message else ConsoleTextSink._event_label(event, payload)
        head = f'{clock} {level:<5} {service} {label}'
        excluded = {
            'time',
            'level',
            'application',
            'environment',
            'service',
            'event',
            'message',
            'status',
            'diagnostic_available',
            'cause_type',
            'cause_message',
        }
        details: list[str] = []
        for key, value in payload.items():
            if key in excluded:
                continue
            formatted = ConsoleTextSink._format_detail(key, value, payload)
            if formatted is not None:
                details.append(formatted)
        return head if not details else f'{head} | ' + ' | '.join(details)

    @staticmethod
    def _event_label(event: str, payload: Mapping[str, Any]) -> str:
        if event == 'iteration.completed' and payload.get('outcome') == 'skipped':
            return 'iteration skipped'
        return {
            'execution.started': 'started',
            'execution.completed': 'completed',
            'execution.failed': 'failed',
            'execution.cancelled': 'interrupted',
            'iteration.completed': 'iteration completed',
        }.get(event, event)

    @staticmethod
    def _format_detail(
        key: str,
        value: Any,
        payload: Mapping[str, Any],
    ) -> str | None:
        if key == 'run_id':
            return f'run={str(value)[:8]}'
        if key == 'duration_seconds':
            return f'duration={value}s'
        if key == 'source_duration_seconds':
            return f'duration={value}s'
        if key == 'source_last_updated_at_utc':
            return f'source_last_update={value}'
        if key == 'cpu_peak_percent':
            return f'cpu_peak={value}%'
        if key == 'memory_peak_percent':
            return f'memory_peak={value}%'
        if key == 'cpu_limit_cores':
            return f'cpu_limit={value}'
        if key == 'memory_limit_bytes':
            return f'memory_limit={ConsoleTextSink._format_bytes(value)}'
        if key == 'work_iterations':
            return f'work={value}'
        if key == 'empty_iterations':
            return f'empty={value}'
        if key == 'resource_pressure_events':
            return f'pressure={value}'
        if key == 'stop_reason':
            return f'stop={value}'
        if key == 'error_type':
            return f'error={value}'
        if key == 'error_message':
            cause_type = payload.get('cause_type')
            cause_message = payload.get('cause_message')
            if cause_type and cause_message:
                return f'reason={value}; cause={cause_type}: {cause_message}'
            return f'reason={value}'
        if key == 'diagnostic_ref':
            return f'diagnostic={value}'
        if isinstance(value, bool):
            return f'{key}={str(value).lower()}'
        return f'{key}={value}'

    @staticmethod
    def _format_bytes(value: Any) -> str:
        if isinstance(value, int | float) and not isinstance(value, bool):
            mebibytes = float(value) / (1024 * 1024)
            return f'{mebibytes:.1f}MiB'
        return str(value)


class MemoryEventSink(EventSink):
    """Mantiene una cantidad acotada de eventos para diagnóstico y pruebas."""

    def __init__(
        self,
        *,
        max_events: int = 1000,
        projection: EventProjection | None = None,
    ) -> None:
        if isinstance(max_events, bool) or not isinstance(max_events, int):
            raise TypeError('max_events must be an int')
        if max_events <= 0:
            raise ValueError('max_events must be greater than zero')
        if projection is not None and not isinstance(projection, EventProjection):
            raise TypeError('projection must be an EventProjection')
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._projection = projection or FullEventProjection()
        self._lock = threading.Lock()

    @property
    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(list(self._events))

    def emit(
        self,
        event: ObservabilityEvent,
        settings: ObservabilitySettings,
        sanitizer: DataSanitizer,
    ) -> None:
        payload = event.to_dict(settings=settings, sanitizer=sanitizer)
        projected = self._projection.project(event, payload)
        if projected is None:
            return
        # La cola queda acotada y events devuelve nuevas copias para aislar a los consumidores.
        with self._lock:
            self._events.append(projected)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


class CompositeEventSink(EventSink):
    """Entrega el mismo evento a sinks con políticas independientes."""

    def __init__(self, sinks: Iterable[EventSink], *, strict: bool = False) -> None:
        if isinstance(sinks, str | bytes) or not isinstance(sinks, Iterable):
            raise TypeError('sinks must be an iterable of EventSink')
        self._sinks = tuple(sinks)
        if not self._sinks:
            raise ValueError('at least one sink is required')
        if any(not isinstance(sink, EventSink) for sink in self._sinks):
            raise TypeError('sinks must contain only EventSink instances')
        if not isinstance(strict, bool):
            raise TypeError('strict must be a bool')
        self._strict = strict
        self._failure_count = 0
        self._lock = threading.Lock()

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    def emit(
        self,
        event: ObservabilityEvent,
        settings: ObservabilitySettings,
        sanitizer: DataSanitizer,
    ) -> None:
        for sink in self._sinks:
            # En modo normal un destino defectuoso no bloquea a los demás ni al proceso observado.
            try:
                sink.emit(event, settings, sanitizer)
            except Exception:
                with self._lock:
                    self._failure_count += 1
                if self._strict:
                    raise

    def close(self) -> None:
        first_error: Exception | None = None
        for sink in reversed(self._sinks):
            try:
                sink.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if self._strict and first_error is not None:
            raise first_error


def _validate_projection_input(
    event: ObservabilityEvent,
    payload: Mapping[str, Any],
) -> None:
    # Todas las proyecciones públicas comparten la misma frontera de tipos.
    if not isinstance(event, ObservabilityEvent):
        raise TypeError('event must be an ObservabilityEvent')
    if not isinstance(payload, Mapping):
        raise TypeError('payload must be a mapping')


def _validate_frozenset(value: Any, item_type: type, name: str) -> None:
    if not isinstance(value, frozenset):
        raise TypeError(f'{name} must be a frozenset')
    if any(not isinstance(item, item_type) for item in value):
        raise TypeError(f'{name} contains an invalid value')
