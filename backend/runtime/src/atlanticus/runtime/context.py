"""Contexto liviano entregado a cada iteración del job."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import TypeVar, cast

from atlanticus.observability import ObservabilityLogger, get_observability_logger
from atlanticus.runtime.configuration import RuntimeConfiguration
from atlanticus.runtime.definition import JobDefinition
from atlanticus.runtime.errors import RuntimeCancellationRequested
from atlanticus.runtime.summary import OperationalSummary, OperationalValue

T = TypeVar('T')


@dataclass(slots=True)
class JobRuntimeContext:
    """Expone identidad, presupuesto temporal y memoria efímera del proceso."""

    definition: JobDefinition
    configuration: RuntimeConfiguration
    run_id: str
    correlation_id: str
    started_monotonic: float
    deadline_monotonic: float
    safe_deadline_monotonic: float
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    logger: ObservabilityLogger = field(
        default_factory=lambda: get_observability_logger('atlanticus.runtime')
    )
    iteration: int = 0
    stop_reason: str | None = None
    _stop: Event = field(default_factory=Event, repr=False)
    _memory: dict[str, object] = field(default_factory=dict, repr=False)
    _execution_summary: OperationalSummary = field(default_factory=OperationalSummary, repr=False)
    _iteration_summary: OperationalSummary = field(default_factory=OperationalSummary, repr=False)
    _iteration_has_work: bool = field(default=False, repr=False)
    _next_iteration_delay_seconds: float | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.definition, JobDefinition):
            raise TypeError('definition must be a JobDefinition')
        if not isinstance(self.configuration, RuntimeConfiguration):
            raise TypeError('configuration must be a RuntimeConfiguration')
        _require_non_empty_string(self.run_id, 'run_id')
        _require_non_empty_string(self.correlation_id, 'correlation_id')
        for name in (
            'started_monotonic',
            'deadline_monotonic',
            'safe_deadline_monotonic',
        ):
            _require_finite_number(getattr(self, name), name)
        if not self.started_monotonic <= self.safe_deadline_monotonic <= self.deadline_monotonic:
            raise ValueError('runtime deadlines must be ordered')
        if not callable(self.clock):
            raise TypeError('clock must be callable')
        if not isinstance(self.logger, ObservabilityLogger):
            raise TypeError('logger must be an ObservabilityLogger')
        if isinstance(self.iteration, bool) or not isinstance(self.iteration, int):
            raise TypeError('iteration must be an int')
        if self.iteration < 0:
            raise ValueError('iteration must be greater than or equal to zero')
        if self.stop_reason is not None:
            _validate_stop_reason(self.stop_reason)

    @classmethod
    def create(
        cls,
        *,
        definition: JobDefinition,
        configuration: RuntimeConfiguration,
        run_id: str,
        correlation_id: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> JobRuntimeContext:
        started = clock()
        _require_finite_number(started, 'clock result')
        return cls(
            definition=definition,
            configuration=configuration,
            run_id=run_id,
            correlation_id=correlation_id,
            started_monotonic=started,
            deadline_monotonic=started + definition.execution_timeout_seconds,
            safe_deadline_monotonic=started + definition.safe_execution_seconds,
            clock=clock,
            logger=get_observability_logger(definition.module_name),
        )

    @property
    def application(self) -> str:
        return self.configuration.application

    @property
    def service_name(self) -> str:
        return self.definition.service_name

    @property
    def module_name(self) -> str:
        return self.definition.module_name

    @property
    def volume_path(self) -> Path:
        return self.configuration.volume_path

    @property
    def application_root(self) -> Path:
        return self.configuration.application_root

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - self.clock())

    @property
    def safe_remaining_seconds(self) -> float:
        return max(0.0, self.safe_deadline_monotonic - self.clock())

    @property
    def should_stop(self) -> bool:
        return self._stop.is_set() or self.safe_remaining_seconds <= 0

    def request_stop(self, reason: str = 'requested') -> None:
        normalized_reason = _validate_stop_reason(reason)
        if self.stop_reason is None:
            self.stop_reason = normalized_reason
        self._stop.set()

    def raise_if_cancelled(self) -> None:
        if self.should_stop:
            raise RuntimeCancellationRequested(self.stop_reason or 'safe_execution_window_elapsed')

    def wait(self, seconds: float) -> bool:
        """Espera de forma interrumpible sin cruzar el límite seguro."""

        _require_finite_number(seconds, 'seconds')
        if seconds < 0:
            raise ValueError('seconds must be greater than or equal to zero')
        allowed = min(seconds, self.safe_remaining_seconds)
        if allowed > 0:
            self._stop.wait(allowed)
        return not self.should_stop

    def get_memory(self, key: str, default: T | None = None) -> T | None:
        return cast(T | None, self._memory.get(_normalize_memory_key(key), default))

    def set_memory(self, key: str, value: object) -> None:
        normalized = _normalize_memory_key(key)
        self._memory[normalized] = value

    def get_or_create(self, key: str, factory: Callable[[], T]) -> T:
        normalized = _normalize_memory_key(key)
        if not callable(factory):
            raise TypeError('factory must be callable')
        if normalized not in self._memory:
            self._memory[normalized] = factory()
        return cast(T, self._memory[normalized])

    def clear_memory(self) -> None:
        self._memory.clear()

    def set_execution_fact(self, key: str, value: OperationalValue) -> None:
        self._execution_summary.set(key, value)

    def increment_execution_counter(self, key: str, amount: int | float = 1) -> None:
        self._execution_summary.increment(key, amount)

    def get_execution_fact(
        self,
        key: str,
        default: OperationalValue | None = None,
    ) -> OperationalValue | None:
        return self._execution_summary.get(key, default)

    def set_iteration_fact(self, key: str, value: OperationalValue) -> None:
        self._iteration_summary.set(key, value)

    def increment_iteration_counter(self, key: str, amount: int | float = 1) -> None:
        self._iteration_summary.increment(key, amount)

    def get_iteration_fact(
        self,
        key: str,
        default: OperationalValue | None = None,
    ) -> OperationalValue | None:
        return self._iteration_summary.get(key, default)

    def mark_iteration_work(self) -> None:
        self._iteration_has_work = True

    def set_next_iteration_delay(self, seconds: float) -> None:
        _require_finite_number(seconds, 'seconds')
        if seconds < 0:
            raise ValueError('seconds must be greater than or equal to zero')
        self._next_iteration_delay_seconds = float(seconds)

    def _next_iteration_delay(self) -> float | None:
        return self._next_iteration_delay_seconds

    def _execution_facts(self) -> dict[str, OperationalValue]:
        return self._execution_summary.snapshot()

    def _iteration_facts(self) -> dict[str, OperationalValue]:
        return self._iteration_summary.snapshot()

    @property
    def iteration_has_work(self) -> bool:
        return self._iteration_has_work

    def _begin_iteration(self, iteration: int) -> None:
        if isinstance(iteration, bool) or not isinstance(iteration, int):
            raise TypeError('iteration must be an int')
        if iteration <= 0:
            raise ValueError('iteration must be greater than zero')
        self.iteration = iteration
        self._iteration_summary.clear()
        self._iteration_has_work = False
        self._next_iteration_delay_seconds = None


def _require_non_empty_string(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f'{name} must be a string')
    if not value.strip():
        raise ValueError(f'{name} must not be empty')
    return value


def _require_finite_number(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f'{name} must be an int or float')
    if not math.isfinite(value):
        raise ValueError(f'{name} must be finite')


def _validate_stop_reason(reason: str) -> str:
    _require_non_empty_string(reason, 'reason')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', reason):
        raise ValueError('reason must use lower snake_case')
    return reason


def _normalize_memory_key(key: str) -> str:
    _require_non_empty_string(key, 'key')
    return key.strip()
