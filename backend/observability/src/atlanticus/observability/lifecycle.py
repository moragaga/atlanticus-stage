"""Scopes para medir tiempos globales e internos de una ejecución."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from atlanticus.kernel import OperationStatus
from atlanticus.observability.context import execution_scope, iteration_scope
from atlanticus.observability.models import (
    ErrorInfo,
    EventAudience,
    EventCategory,
    EventSeverity,
    ExecutionContext,
    ObservabilityEvent,
)
from atlanticus.observability.state import emit_event, trace_span


@contextmanager
def trace_execution(
    *,
    run_id: str | None = None,
    correlation_id: str | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[ExecutionContext]:
    """Mide la ejecución completa y conserva el error original."""

    with execution_scope(run_id=run_id, correlation_id=correlation_id) as context:
        base_attributes = dict(attributes or {})
        with trace_span(
            'execution',
            attributes={'atlanticus.span_kind': 'execution', **base_attributes},
        ):
            emit_event(
                ObservabilityEvent(
                    name='execution.started',
                    category=EventCategory.LIFECYCLE,
                    attributes=base_attributes,
                )
            )
            started = time.perf_counter()
            try:
                yield context
            except BaseException as error:
                emit_event(
                    ObservabilityEvent(
                        name='execution.failed',
                        category=EventCategory.LIFECYCLE,
                        severity=EventSeverity.ERROR,
                        status=OperationStatus.ERROR,
                        duration_ms=(time.perf_counter() - started) * 1000,
                        attributes=base_attributes,
                        error=ErrorInfo.from_exception(error),
                    )
                )
                raise
            emit_event(
                ObservabilityEvent(
                    name='execution.finished',
                    category=EventCategory.LIFECYCLE,
                    status=OperationStatus.SUCCESS,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    attributes=base_attributes,
                )
            )


@contextmanager
def trace_iteration(
    iteration: int,
    *,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[ExecutionContext]:
    """Mide una iteración interna y la enlaza con su ejecución activa."""

    with iteration_scope(iteration) as context:
        base_attributes = dict(attributes or {})
        with trace_span(
            'iteration',
            attributes={'atlanticus.span_kind': 'iteration', **base_attributes},
        ):
            emit_event(
                ObservabilityEvent(
                    name='iteration.started',
                    category=EventCategory.ITERATION,
                    attributes=base_attributes,
                )
            )
            started = time.perf_counter()
            try:
                yield context
            except BaseException as error:
                emit_event(
                    ObservabilityEvent(
                        name='iteration.failed',
                        category=EventCategory.ITERATION,
                        severity=EventSeverity.ERROR,
                        status=OperationStatus.ERROR,
                        duration_ms=(time.perf_counter() - started) * 1000,
                        attributes=base_attributes,
                        error=ErrorInfo.from_exception(error),
                    )
                )
                raise
            emit_event(
                ObservabilityEvent(
                    name='iteration.finished',
                    category=EventCategory.ITERATION,
                    audience=EventAudience.OPERATIONS,
                    status=OperationStatus.SUCCESS,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    attributes=base_attributes,
                )
            )
