"""Modelos públicos y serializables de observabilidad."""

from __future__ import annotations

import math
import os
import socket
import traceback as traceback_module
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from atlanticus.kernel import DataSanitizer, Environment, OperationStatus, utc_now


class EventCategory(StrEnum):
    """Familias estables utilizadas para clasificar eventos."""

    LIFECYCLE = 'lifecycle'
    ITERATION = 'iteration'
    DEPENDENCY = 'dependency'
    DATA = 'data'
    RESOURCE = 'resource'
    DIAGNOSTIC = 'diagnostic'
    CONCURRENCY = 'concurrency'


class EventSeverity(StrEnum):
    """Niveles independientes del backend de logging utilizado por un sink."""

    DEBUG = 'debug'
    INFO = 'info'
    WARNING = 'warning'
    ERROR = 'error'
    CRITICAL = 'critical'


class EventAudience(StrEnum):
    """Destinatarios semánticos sin nombrar un proveedor de observabilidad."""

    LOCAL = 'local'
    OPERATIONS = 'operations'


_CONTEXT_TEXT_FIELDS = (
    'application',
    'service',
    'module',
    'component',
    'environment',
    'instance_id',
    'run_id',
    'correlation_id',
    'operation_id',
    'parent_operation_id',
    'concurrency_scope',
    'task_id',
    'worker_kind',
    'target_alias',
    'concurrency_group',
)
_CONTEXT_POSITIVE_INTEGER_FIELDS = ('process_id', 'iteration', 'attempt')
_CONTEXT_NON_NEGATIVE_INTEGER_FIELDS = ('worker_index',)


def _require_non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a non-empty string')
    return value


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            _require_non_empty_string(key, 'mapping key')
            frozen[key] = _freeze_value(item)
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze_value(item) for item in value)
    if isinstance(
        value,
        str | bytes | int | float | bool | type(None) | datetime | date | time | timedelta,
    ):
        return value
    if isinstance(value, Decimal | UUID | Path | Enum):
        return value
    return deepcopy(value)


def _freeze_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f'{name} must be a mapping')
    return _freeze_value(value)


def _exception_traceback(error: BaseException) -> str | None:
    sections: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        frames = traceback_module.extract_tb(current.__traceback__)
        if frames:
            sections.append(f'{type(current).__name__}:')
            sections.extend(
                f'  File {frame.filename!r}, line {frame.lineno}, in {frame.name}'
                for frame in frames
            )
        current = current.__cause__
    return '\n'.join(sections) or None


def _root_cause(error: BaseException) -> BaseException:
    current = error
    seen = {id(current)}
    while current.__cause__ is not None and id(current.__cause__) not in seen:
        current = current.__cause__
        seen.add(id(current))
    return current


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Identidad opcional heredada por los eventos de una ejecución."""

    application: str | None = None
    service: str | None = None
    module: str | None = None
    component: str | None = None
    environment: str | None = None
    instance_id: str | None = None
    process_id: int | None = None
    run_id: str | None = None
    correlation_id: str | None = None
    operation_id: str | None = None
    parent_operation_id: str | None = None
    iteration: int | None = None
    attempt: int | None = None
    concurrency_scope: str | None = None
    task_id: str | None = None
    worker_kind: str | None = None
    worker_index: int | None = None
    target_alias: str | None = None
    concurrency_group: str | None = None

    def __post_init__(self) -> None:
        for name in _CONTEXT_TEXT_FIELDS:
            value = getattr(self, name)
            if value is not None:
                _require_non_empty_string(value, name)
        for name in _CONTEXT_POSITIVE_INTEGER_FIELDS:
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise TypeError(f'{name} must be an int')
            if value is not None and value <= 0:
                raise ValueError(f'{name} must be greater than zero')
        for name in _CONTEXT_NON_NEGATIVE_INTEGER_FIELDS:
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise TypeError(f'{name} must be an int')
            if value is not None and value < 0:
                raise ValueError(f'{name} must be greater than or equal to zero')

    def to_dict(self) -> dict[str, Any]:
        """Retorna únicamente campos definidos para mantener pequeño el payload."""

        return {
            key: value
            for key, value in (
                ('application', self.application),
                ('service', self.service),
                ('module', self.module),
                ('component', self.component),
                ('environment', self.environment),
                ('instance_id', self.instance_id),
                ('process_id', self.process_id),
                ('run_id', self.run_id),
                ('correlation_id', self.correlation_id),
                ('operation_id', self.operation_id),
                ('parent_operation_id', self.parent_operation_id),
                ('iteration', self.iteration),
                ('attempt', self.attempt),
                ('concurrency_scope', self.concurrency_scope),
                ('task_id', self.task_id),
                ('worker_kind', self.worker_kind),
                ('worker_index', self.worker_index),
                ('target_alias', self.target_alias),
                ('concurrency_group', self.concurrency_group),
            )
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """Información acotada de un error sin conservar la excepción original."""

    error_type: str
    message: str
    code: str | None = None
    traceback: str | None = None
    retryable: bool | None = None
    cause_type: str | None = None
    cause_message: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.error_type, 'error_type')
        _require_non_empty_string(self.message, 'message')
        for name in ('code', 'traceback', 'cause_type', 'cause_message'):
            value = getattr(self, name)
            if value is not None:
                _require_non_empty_string(value, name)
        if self.retryable is not None and not isinstance(self.retryable, bool):
            raise TypeError('retryable must be a bool')
        if (self.cause_type is None) != (self.cause_message is None):
            raise ValueError('cause_type and cause_message must be provided together')

    @classmethod
    def from_exception(cls, error: BaseException) -> ErrorInfo:
        """Construye un diagnóstico sin copiar mensajes potencialmente sensibles."""

        if not isinstance(error, BaseException):
            raise TypeError('error must be a BaseException')
        root = _root_cause(error)
        error_type = type(error).__name__
        return cls(
            error_type=error_type,
            message=f'{error_type} raised',
            traceback=_exception_traceback(error),
            cause_type=type(root).__name__ if root is not error else None,
            cause_message=(f'{type(root).__name__} raised' if root is not error else None),
        )

    def to_dict(self, sanitizer: DataSanitizer) -> dict[str, Any]:
        """Sanitiza texto y omite valores ausentes."""

        return {
            key: value
            for key, value in {
                'type': sanitizer.sanitize(self.error_type),
                'message': sanitizer.sanitize(self.message),
                'code': sanitizer.sanitize(self.code) if self.code is not None else None,
                'traceback': (
                    sanitizer.sanitize(self.traceback) if self.traceback is not None else None
                ),
                'retryable': self.retryable,
                'cause_type': (
                    sanitizer.sanitize(self.cause_type) if self.cause_type is not None else None
                ),
                'cause_message': (
                    sanitizer.sanitize(self.cause_message)
                    if self.cause_message is not None
                    else None
                ),
            }.items()
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class ObservabilitySettings:
    """Identidad y rutas utilizadas al materializar eventos."""

    application: str
    service: str
    component: str
    environment: Environment
    module: str | None = None
    instance_id: str = field(default_factory=socket.gethostname)
    process_id: int = field(default_factory=os.getpid)
    volume_path: Path | None = None

    def __post_init__(self) -> None:
        for name in ('application', 'service', 'component', 'instance_id'):
            _require_non_empty_string(getattr(self, name), name)
        if self.module is not None:
            _require_non_empty_string(self.module, 'module')
        if not isinstance(self.environment, Environment):
            raise TypeError('environment must be an Environment')
        if isinstance(self.process_id, bool) or not isinstance(self.process_id, int):
            raise TypeError('process_id must be an int')
        if self.process_id <= 0:
            raise ValueError('process_id must be greater than zero')
        if self.volume_path is not None and not isinstance(self.volume_path, Path):
            raise TypeError('volume_path must be a Path')

    @classmethod
    def build(
        cls,
        *,
        application: str,
        service: str,
        component: str = 'runtime',
        environment: Environment | str,
        module: str | None = None,
        instance_id: str | None = None,
        process_id: int | None = None,
        volume_path: str | Path | None = None,
    ) -> ObservabilitySettings:
        """Construye settings sin normalizar silenciosamente el ambiente."""

        resolved_environment = (
            environment
            if isinstance(environment, Environment)
            else Environment.from_value(environment)
        )
        values: dict[str, Any] = {
            'application': application,
            'service': service,
            'component': component,
            'environment': resolved_environment,
            'module': module,
            'volume_path': Path(volume_path) if volume_path is not None else None,
        }
        if instance_id is not None:
            values['instance_id'] = instance_id
        if process_id is not None:
            values['process_id'] = process_id
        return cls(**values)

    def base_context(self) -> ExecutionContext:
        """Crea el contexto base que puede ampliarse mediante scopes."""

        return ExecutionContext(
            application=self.application,
            service=self.service,
            module=self.module,
            component=self.component,
            environment=str(self.environment),
            instance_id=self.instance_id,
            process_id=self.process_id,
        )


@dataclass(frozen=True, slots=True)
class ObservabilityEvent:
    """Sobre neutral de un hecho operacional ocurrido una sola vez."""

    name: str
    category: EventCategory
    audience: EventAudience = EventAudience.LOCAL
    severity: EventSeverity = EventSeverity.INFO
    status: OperationStatus | str | None = None
    message: str | None = None
    context: ExecutionContext | None = None
    duration_ms: float | None = None
    metrics: Mapping[str, int | float] = field(default_factory=dict)
    attributes: Mapping[str, Any] = field(default_factory=dict)
    error: ErrorInfo | None = None
    event_id: UUID = field(default_factory=uuid4)
    occurred_at_utc: datetime = field(default_factory=utc_now)
    schema_version: int = 2

    def __post_init__(self) -> None:
        normalized_name = _require_non_empty_string(self.name, 'event name').strip()
        if not isinstance(self.category, EventCategory):
            raise TypeError('category must be an EventCategory')
        if not isinstance(self.audience, EventAudience):
            raise TypeError('audience must be an EventAudience')
        if not isinstance(self.severity, EventSeverity):
            raise TypeError('severity must be an EventSeverity')
        if self.status is not None and not isinstance(self.status, OperationStatus | str):
            raise TypeError('status must be an OperationStatus or string')
        if isinstance(self.status, str):
            _require_non_empty_string(self.status, 'status')
        if self.message is not None:
            _require_non_empty_string(self.message, 'message')
        if self.context is not None and not isinstance(self.context, ExecutionContext):
            raise TypeError('context must be an ExecutionContext')
        if self.error is not None and not isinstance(self.error, ErrorInfo):
            raise TypeError('error must be an ErrorInfo')
        if not isinstance(self.event_id, UUID):
            raise TypeError('event_id must be a UUID')
        if not isinstance(self.occurred_at_utc, datetime):
            raise TypeError('occurred_at_utc must be a datetime')
        if self.occurred_at_utc.tzinfo is None or self.occurred_at_utc.utcoffset() is None:
            raise ValueError('occurred_at_utc must be timezone-aware')
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int):
            raise TypeError('schema_version must be an int')
        if self.schema_version <= 0:
            raise ValueError('schema_version must be greater than zero')
        if self.duration_ms is not None and isinstance(self.duration_ms, bool):
            raise TypeError('duration_ms must be an int or float')
        if self.duration_ms is not None and not isinstance(self.duration_ms, int | float):
            raise TypeError('duration_ms must be an int or float')
        if self.duration_ms is not None and (
            not math.isfinite(self.duration_ms) or self.duration_ms < 0
        ):
            raise ValueError('duration_ms must be a finite value greater than or equal to zero')

        metrics = _freeze_mapping(self.metrics, 'metrics')
        cleaned_metrics: dict[str, int | float] = {}
        for key, value in metrics.items():
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise TypeError(f'metric {key!r} must be an int or float')
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f'metric {key!r} must be finite')
            cleaned_metrics[key] = value

        object.__setattr__(self, 'name', normalized_name)
        object.__setattr__(self, 'metrics', MappingProxyType(cleaned_metrics))
        object.__setattr__(self, 'attributes', _freeze_mapping(self.attributes, 'attributes'))

    @property
    def status_value(self) -> str | None:
        """Retorna un estado serializable sin ampliar el enum del kernel."""

        if self.status is None:
            return None
        if isinstance(self.status, OperationStatus):
            return self.status.value
        return str(self.status)

    def to_dict(
        self,
        *,
        settings: ObservabilitySettings,
        sanitizer: DataSanitizer,
    ) -> dict[str, Any]:
        """Materializa el evento con identidad, métricas y atributos sanitizados."""

        context = self.context or settings.base_context()
        payload: dict[str, Any] = {
            'schema_version': self.schema_version,
            'event_id': str(self.event_id),
            'occurred_at_utc': self.occurred_at_utc.isoformat(),
            'name': self.name,
            'category': self.category.value,
            'audience': self.audience.value,
            'severity': self.severity.value,
            'status': self.status_value,
            'message': sanitizer.sanitize(self.message) if self.message is not None else None,
            'context': sanitizer.sanitize(context.to_dict()),
            'duration_ms': round(self.duration_ms, 3) if self.duration_ms is not None else None,
            'metrics': dict(self.metrics) or None,
            'attributes': sanitizer.sanitize(dict(self.attributes)) if self.attributes else None,
            'error': self.error.to_dict(sanitizer) if self.error is not None else None,
        }
        return {key: value for key, value in payload.items() if value is not None}


@dataclass(frozen=True, slots=True)
class ResultSummary:
    """Resumen explícito retornado por un mapper de resultados."""

    metrics: Mapping[str, int | float] = field(default_factory=dict)
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        event = ObservabilityEvent(
            name='result.summary.validation',
            category=EventCategory.DIAGNOSTIC,
            metrics=self.metrics,
            attributes=self.attributes,
        )
        object.__setattr__(self, 'metrics', event.metrics)
        object.__setattr__(self, 'attributes', event.attributes)
