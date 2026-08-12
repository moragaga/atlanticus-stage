"""Proyección operacional compacta compartida por consola, archivos y Azure."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from atlanticus.observability.models import (
    EventAudience,
    EventCategory,
    EventSeverity,
    ObservabilityEvent,
)
from atlanticus.observability.sinks import EventProjection

_OPERATIONAL_NAMES = frozenset(
    {
        'execution.started',
        'runtime.execution.summary',
        'runtime.iteration.summary',
        'execution.timed_out',
        'execution.cancelled',
        'resource.pressure.started',
        'resource.pressure.escalated',
        'resource.pressure.recovered',
        'resource.pressure.open_at_monitor_stop',
        'observability.azure.bootstrap.failed',
        'dependency.slow',
        'dependency.failed',
    }
)
_DENIED_OPERATIONAL_NAMES = frozenset({'resource.pressure.ongoing'})
_EVENT_ALIASES = {
    'runtime.execution.summary': 'execution.completed',
    'runtime.iteration.summary': 'iteration.completed',
}
_IGNORED_FIELDS = frozenset(
    {
        'component',
        'credential_scope',
        'cpu_source',
        'execution_timeout_seconds',
        'iteration_timeout_seconds',
        'lease_timeout_seconds',
        'max_wait_time_seconds',
        'memory_source',
        'read_mode',
        'receive_mode',
        'recovered_expired_lease',
        'run_once',
        'shutdown_grace_seconds',
    }
)
_SENSITIVE_KEY_PARTS = (
    'password',
    'passwd',
    'pwd',
    'secret',
    'token',
    'credential',
    'connection_string',
    'access_key',
    'api_key',
)
_RESERVED_FIELDS = frozenset(
    {
        'time',
        'level',
        'application',
        'cause_message',
        'cause_type',
        'environment',
        'service',
        'event',
        'message',
        'run_id',
        'iteration',
        'status',
        'duration_seconds',
        'error_type',
        'error_message',
        'error_code',
        'retryable',
        'diagnostic_available',
        'diagnostic_ref',
    }
)


class OperationalEventProjection(EventProjection):
    """Reduce eventos internos a hechos operacionales estables y planos."""

    def includes(self, event: ObservabilityEvent) -> bool:
        if not isinstance(event, ObservabilityEvent):
            raise TypeError('event must be an ObservabilityEvent')
        if event.name in _DENIED_OPERATIONAL_NAMES:
            return False
        if event.name in _OPERATIONAL_NAMES:
            return True
        if event.severity in {EventSeverity.WARNING, EventSeverity.ERROR, EventSeverity.CRITICAL}:
            return event.category is not EventCategory.DEPENDENCY
        return event.audience is EventAudience.OPERATIONS and event.category is EventCategory.DATA

    def project(
        self,
        event: ObservabilityEvent,
        payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if not isinstance(payload, Mapping):
            raise TypeError('payload must be a mapping')
        if not self.includes(event):
            return None
        for key in ('occurred_at_utc', 'severity'):
            if key not in payload:
                raise ValueError(f'payload must contain {key!r}')

        context = payload.get('context')
        context_values = context if isinstance(context, Mapping) else {}
        projected: dict[str, Any] = {
            'time': payload['occurred_at_utc'],
            'level': payload['severity'],
            'application': context_values.get('application'),
            'environment': context_values.get('environment'),
            'service': context_values.get('service'),
            'event': _EVENT_ALIASES.get(event.name, event.name),
        }
        message = payload.get('message')
        if isinstance(message, str) and message:
            projected['message'] = message
        run_id = context_values.get('run_id')
        if run_id is not None:
            projected['run_id'] = run_id
        iteration = context_values.get('iteration')
        if iteration is not None:
            projected['iteration'] = iteration
        status = payload.get('status')
        if status is not None:
            projected['status'] = status
        elif event.name == 'execution.started':
            projected['status'] = 'running'
        duration_ms = payload.get('duration_ms')
        if isinstance(duration_ms, int | float) and not isinstance(duration_ms, bool):
            projected['duration_seconds'] = round(float(duration_ms) / 1000, 3)

        self._merge_values(projected, payload.get('metrics'))
        self._merge_values(projected, payload.get('attributes'))
        self._merge_error(projected, payload.get('error'))
        return {key: value for key, value in projected.items() if value is not None}

    @classmethod
    def _merge_values(cls, destination: dict[str, Any], values: Any) -> None:
        if not isinstance(values, Mapping):
            return
        for raw_key, value in values.items():
            key = str(raw_key)
            if key in destination or key in _RESERVED_FIELDS or cls._ignored_key(key, value):
                continue
            destination[key] = deepcopy(value)

    @staticmethod
    def _merge_error(destination: dict[str, Any], error: Any) -> None:
        if not isinstance(error, Mapping):
            return
        error_type = error.get('type')
        error_message = error.get('message')
        if error_type is not None:
            destination['error_type'] = error_type
        if error_message is not None:
            destination['error_message'] = error_message
        if error.get('code') is not None:
            destination['error_code'] = error['code']
        if error.get('retryable') is not None:
            destination['retryable'] = error['retryable']
        if error.get('cause_type') is not None:
            destination['cause_type'] = error['cause_type']
        if error.get('cause_message') is not None:
            destination['cause_message'] = error['cause_message']
        if error.get('traceback'):
            destination['diagnostic_available'] = True
            run_id = destination.get('run_id')
            if run_id:
                destination['diagnostic_ref'] = f'run:{run_id}'

    @staticmethod
    def _ignored_key(key: str, value: Any) -> bool:
        normalized = key.lower()
        if key in _IGNORED_FIELDS:
            return True
        if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
            return True
        return value == '***redacted***'
