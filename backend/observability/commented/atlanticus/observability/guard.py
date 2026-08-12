"""Decorador para contratos externos sin modificar su resultado ni sus errores."""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any, ParamSpec, TypeVar, cast

from atlanticus.kernel import OperationStatus
from atlanticus.observability.context import operation_scope
from atlanticus.observability.models import (
    ErrorInfo,
    EventAudience,
    EventCategory,
    EventSeverity,
    ObservabilityEvent,
    ResultSummary,
)
from atlanticus.observability.state import emit_event, trace_span

P = ParamSpec('P')
R = TypeVar('R')
ParameterMapper = Callable[[tuple[Any, ...], Mapping[str, Any]], Mapping[str, Any]]
ResultMapper = Callable[[Any], ResultSummary]
ErrorMapper = Callable[[BaseException], ErrorInfo]
_RESERVED_DEPENDENCY_ATTRIBUTES = frozenset(
    {'operation', 'component', 'atlanticus.span_kind'}
)


def _default_error(error: BaseException) -> ErrorInfo:
    # Ninguna ruta automática debe copiar str(error); todos los fallos usan la fábrica segura.
    return ErrorInfo.from_exception(error)


def runtime_guard(
    *,
    operation: str,
    component: str,
    target_alias: str | None = None,
    concurrency_group: str | None = None,
    parameter_mapper: ParameterMapper | None = None,
    result_mapper: ResultMapper | None = None,
    error_mapper: ErrorMapper | None = None,
    slow_after_ms: float | None = None,
    emit_started: bool = True,
    audience: EventAudience = EventAudience.LOCAL,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Traza una llamada externa y siempre propaga su resultado o excepción original."""

    # Se valida la configuración al crear el decorador, antes de ejecutar el contrato de negocio.
    if not isinstance(operation, str) or not operation.strip():
        raise ValueError('operation must not be empty')
    if not isinstance(component, str) or not component.strip():
        raise ValueError('component must not be empty')
    for name, value in (
        ('target_alias', target_alias),
        ('concurrency_group', concurrency_group),
    ):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f'{name} must be a non-empty string')
    for name, mapper in (
        ('parameter_mapper', parameter_mapper),
        ('result_mapper', result_mapper),
        ('error_mapper', error_mapper),
    ):
        if mapper is not None and not callable(mapper):
            raise TypeError(f'{name} must be callable')
    if slow_after_ms is not None and (
        isinstance(slow_after_ms, bool) or not isinstance(slow_after_ms, int | float)
    ):
        raise TypeError('slow_after_ms must be an int or float')
    if slow_after_ms is not None and slow_after_ms <= 0:
        raise ValueError('slow_after_ms must be greater than zero')
    if not isinstance(emit_started, bool):
        raise TypeError('emit_started must be a bool')
    if not isinstance(audience, EventAudience):
        raise TypeError('audience must be an EventAudience')

    def decorator(function: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                with operation_scope(
                    target_alias=target_alias,
                    concurrency_group=concurrency_group,
                ):
                    attributes = _parameter_attributes(parameter_mapper, args, kwargs)
                    with trace_span(
                        operation,
                        attributes={
                            **_dependency_attributes(operation, component, attributes),
                            'atlanticus.span_kind': 'dependency',
                        },
                    ):
                        _emit_started(operation, component, attributes, emit_started, audience)
                        started = time.perf_counter()
                        try:
                            result = await function(*args, **kwargs)
                        except BaseException as error:
                            _emit_failure(
                                operation=operation,
                                component=component,
                                duration_ms=(time.perf_counter() - started) * 1000,
                                attributes=attributes,
                                error=_map_error(error_mapper, error),
                                audience=audience,
                            )
                            raise
                        _emit_success(
                            operation=operation,
                            component=component,
                            duration_ms=(time.perf_counter() - started) * 1000,
                            attributes=attributes,
                            summary=_map_result(result_mapper, result),
                            slow_after_ms=slow_after_ms,
                            audience=audience,
                        )
                        return result

            return cast(Callable[P, R], async_wrapper)

        @wraps(function)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with operation_scope(
                target_alias=target_alias,
                concurrency_group=concurrency_group,
            ):
                attributes = _parameter_attributes(parameter_mapper, args, kwargs)
                with trace_span(
                    operation,
                    attributes={
                        **_dependency_attributes(operation, component, attributes),
                        'atlanticus.span_kind': 'dependency',
                    },
                ):
                    _emit_started(operation, component, attributes, emit_started, audience)
                    started = time.perf_counter()
                    try:
                        result = function(*args, **kwargs)
                    except BaseException as error:
                        _emit_failure(
                            operation=operation,
                            component=component,
                            duration_ms=(time.perf_counter() - started) * 1000,
                            attributes=attributes,
                            error=_map_error(error_mapper, error),
                            audience=audience,
                        )
                        raise
                    _emit_success(
                        operation=operation,
                        component=component,
                        duration_ms=(time.perf_counter() - started) * 1000,
                        attributes=attributes,
                        summary=_map_result(result_mapper, result),
                        slow_after_ms=slow_after_ms,
                        audience=audience,
                    )
                    return result

        return sync_wrapper

    return decorator


def _parameter_attributes(
    mapper: ParameterMapper | None,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    if mapper is None:
        return {}
    try:
        mapped = mapper(args, kwargs)
        if not isinstance(mapped, Mapping):
            raise TypeError('parameter_mapper must return a mapping')
        attributes = dict(mapped)
        _validate_attribute_structure(attributes)
    except Exception as error:
        _emit_mapper_failure('parameter_mapper', error)
        return {}
    return {
        key: value
        for key, value in attributes.items()
        if key not in _RESERVED_DEPENDENCY_ATTRIBUTES
    }


def _validate_attribute_structure(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError('observability attribute keys must be non-empty strings')
            _validate_attribute_structure(item)
        return
    if isinstance(value, list | tuple | set | frozenset):
        for item in value:
            _validate_attribute_structure(item)


def _dependency_attributes(
    operation: str,
    component: str,
    *attribute_sets: Mapping[str, Any],
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for attributes in attribute_sets:
        merged.update(
            {
                key: value
                for key, value in attributes.items()
                if key not in _RESERVED_DEPENDENCY_ATTRIBUTES
            }
        )
    merged['operation'] = operation
    merged['component'] = component
    return merged


def _map_result(mapper: ResultMapper | None, result: Any) -> ResultSummary:
    if mapper is None:
        return ResultSummary()
    try:
        summary = mapper(result)
        if not isinstance(summary, ResultSummary):
            raise TypeError('result_mapper must return ResultSummary')
    except Exception as error:
        _emit_mapper_failure('result_mapper', error)
        return ResultSummary()
    else:
        return summary


def _map_error(mapper: ErrorMapper | None, error: BaseException) -> ErrorInfo:
    # Un mapper puede aportar un mensaje seguro. Si falla o rompe el contrato, se degrada al resumen
    # automático sin afectar la excepción original.
    if mapper is None:
        return _default_error(error)
    try:
        mapped = mapper(error)
    except Exception:
        return _default_error(error)
    if not isinstance(mapped, ErrorInfo):
        return _default_error(error)
    return mapped


def _emit_mapper_failure(mapper: str, error: Exception) -> None:
    emit_event(
        ObservabilityEvent(
            name='observability.mapper.failed',
            category=EventCategory.DIAGNOSTIC,
            severity=EventSeverity.WARNING,
            status=OperationStatus.WARNING,
            attributes={'mapper': mapper},
            error=ErrorInfo.from_exception(error),
        )
    )


def _emit_started(
    operation: str,
    component: str,
    attributes: Mapping[str, Any],
    enabled: bool,
    audience: EventAudience,
) -> None:
    if not enabled:
        return
    emit_event(
        ObservabilityEvent(
            name='dependency.started',
            category=EventCategory.DEPENDENCY,
            audience=audience,
            attributes=_dependency_attributes(operation, component, attributes),
        )
    )


def _emit_success(
    *,
    operation: str,
    component: str,
    duration_ms: float,
    attributes: Mapping[str, Any],
    summary: ResultSummary,
    slow_after_ms: float | None,
    audience: EventAudience,
) -> None:
    is_slow = slow_after_ms is not None and duration_ms >= slow_after_ms
    emit_event(
        ObservabilityEvent(
            name='dependency.slow' if is_slow else 'dependency.finished',
            category=EventCategory.DEPENDENCY,
            audience=audience,
            severity=EventSeverity.WARNING if is_slow else EventSeverity.INFO,
            status=OperationStatus.WARNING if is_slow else OperationStatus.SUCCESS,
            duration_ms=duration_ms,
            metrics=dict(summary.metrics),
            attributes=_dependency_attributes(
                operation,
                component,
                attributes,
                summary.attributes,
            ),
        )
    )


def _emit_failure(
    *,
    operation: str,
    component: str,
    duration_ms: float,
    attributes: Mapping[str, Any],
    error: ErrorInfo,
    audience: EventAudience,
) -> None:
    emit_event(
        ObservabilityEvent(
            name='dependency.failed',
            category=EventCategory.DEPENDENCY,
            audience=audience,
            severity=EventSeverity.ERROR,
            status=OperationStatus.ERROR,
            duration_ms=duration_ms,
            attributes=_dependency_attributes(operation, component, attributes),
            error=error,
        )
    )
