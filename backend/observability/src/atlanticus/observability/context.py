"""Scopes de contexto propagables entre funciones, tareas e hilos controlados."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import Context, ContextVar, copy_context
from dataclasses import fields, replace
from functools import wraps
from typing import Any, TypeVar
from uuid import uuid4

from atlanticus.observability.models import ExecutionContext

T = TypeVar('T')
_UNSET = object()
_execution_context: ContextVar[ExecutionContext | None] = ContextVar(
    'atlanticus_execution_context',
    default=None,
)


def get_execution_context() -> ExecutionContext:
    """Retorna el contexto activo, que siempre existe aunque esté vacío."""

    return _execution_context.get() or ExecutionContext()


def set_execution_context(context: ExecutionContext) -> None:
    """Reemplaza el contexto del flujo actual."""

    if not isinstance(context, ExecutionContext):
        raise TypeError('context must be an ExecutionContext')
    _execution_context.set(context)


@contextmanager
def context_scope(**values: Any) -> Iterator[ExecutionContext]:
    """Aplica temporalmente campos conocidos sobre el contexto actual."""

    valid_names = {item.name for item in fields(ExecutionContext)}
    unknown = set(values) - valid_names
    if unknown:
        raise TypeError(f'unknown execution context fields: {sorted(unknown)}')
    updated = replace(get_execution_context(), **values)
    token = _execution_context.set(updated)
    try:
        yield updated
    finally:
        _execution_context.reset(token)


@contextmanager
def execution_scope(
    *,
    run_id: str | None = None,
    correlation_id: str | None = None,
    **values: Any,
) -> Iterator[ExecutionContext]:
    """Crea una ejecución con identificadores nuevos cuando no se entregan."""

    with context_scope(
        run_id=run_id or str(uuid4()),
        correlation_id=correlation_id or str(uuid4()),
        **values,
    ) as context:
        yield context


@contextmanager
def iteration_scope(iteration: int) -> Iterator[ExecutionContext]:
    """Asocia eventos a una iteración positiva."""

    if iteration <= 0:
        raise ValueError('iteration must be greater than zero')
    with context_scope(iteration=iteration) as context:
        yield context


@contextmanager
def operation_scope(
    *,
    operation_id: str | None = None,
    target_alias: str | None | object = _UNSET,
    concurrency_group: str | None | object = _UNSET,
) -> Iterator[ExecutionContext]:
    """Crea una operación hija y preserva el identificador de su padre."""

    current = get_execution_context()
    values: dict[str, Any] = {
        'parent_operation_id': current.operation_id,
        'operation_id': operation_id or str(uuid4()),
    }
    if target_alias is not _UNSET:
        values['target_alias'] = target_alias
    if concurrency_group is not _UNSET:
        values['concurrency_group'] = concurrency_group
    with context_scope(**values) as context:
        yield context


def copied_execution_context() -> Context:
    """Captura el contexto actual para que un runtime pueda transferirlo a un hilo."""

    return copy_context()


def with_current_context(function: Callable[..., T]) -> Callable[..., T]:
    """Envuelve una función para ejecutarla con una copia del contexto actual."""

    captured = copy_context()

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        return captured.copy().run(function, *args, **kwargs)

    return wrapper
