# El contexto expone una autoridad de lease sólo después de que el runner adquiere ownership.
# La generación identifica la época de ownership y el checker permite fallar cerrado antes de un commit durable.

"""Contexto liviano entregado a cada iteración del job."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import TypeVar, cast

from atlanticus.observability import ObservabilityLogger, get_observability_logger
from atlanticus.runtime._window import build_execution_window
from atlanticus.runtime.configuration import RuntimeConfiguration
from atlanticus.runtime.definition import JobDefinition
from atlanticus.runtime.errors import RuntimeCancellationRequested, RuntimeContractError
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
    execution_mode: str
    started_at_utc: datetime
    scheduled_at_utc: datetime | None
    next_scheduled_at_utc: datetime | None
    platform_deadline_utc: datetime | None
    deadline_utc: datetime
    safe_deadline_utc: datetime
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
    _lease_generation: int | None = field(default=None, repr=False)
    _lease_authority_check: Callable[[], None] | None = field(default=None, repr=False)

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
        if self.execution_mode not in {'relative', 'scheduled_external'}:
            raise ValueError('execution_mode must be relative or scheduled_external')
        for name in ('started_at_utc', 'deadline_utc', 'safe_deadline_utc'):
            _require_utc_datetime(getattr(self, name), name)
        for name in ('scheduled_at_utc', 'next_scheduled_at_utc', 'platform_deadline_utc'):
            value = getattr(self, name)
            if value is not None:
                _require_utc_datetime(value, name)
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
        wall_clock: Callable[[], datetime] | None = None,
    ) -> JobRuntimeContext:
        started = clock()
        _require_finite_number(started, 'clock result')
        if wall_clock is not None and not callable(wall_clock):
            raise TypeError('wall_clock must be callable')
        resolved_wall_clock = _utc_now if wall_clock is None else wall_clock
        window = build_execution_window(
            definition=definition,
            configuration=configuration,
            started_at_utc=resolved_wall_clock(),
            started_monotonic=started,
        )
        return cls(
            definition=definition,
            configuration=configuration,
            run_id=run_id,
            correlation_id=correlation_id,
            started_monotonic=window.started_monotonic,
            deadline_monotonic=window.deadline_monotonic,
            safe_deadline_monotonic=window.safe_deadline_monotonic,
            execution_mode=window.mode,
            started_at_utc=window.started_at_utc,
            scheduled_at_utc=window.scheduled_at_utc,
            next_scheduled_at_utc=window.next_scheduled_at_utc,
            platform_deadline_utc=window.platform_deadline_utc,
            deadline_utc=window.deadline_utc,
            safe_deadline_utc=window.safe_deadline_utc,
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

    def _utc_now(self) -> datetime:
        elapsed = max(0.0, self.clock() - self.started_monotonic)
        return self.started_at_utc + timedelta(seconds=elapsed)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - self.clock())

    @property
    def safe_remaining_seconds(self) -> float:
        return max(0.0, self.safe_deadline_monotonic - self.clock())

    @property
    def should_stop(self) -> bool:
        return self._stop.is_set() or self.safe_remaining_seconds <= 0

    # La generación es metadata técnica del runtime; el dominio puede registrarla sin gobernar el lease.
    @property
    def lease_generation(self) -> int | None:
        return self._lease_generation

    # Antes de una escritura autoritativa, el consumidor puede exigir que esta adquisición siga vigente.
    def assert_lease_current(self) -> None:
        checker = self._lease_authority_check
        if self._lease_generation is None or checker is None:
            raise RuntimeContractError('lease authority is not available in this context')
        checker()

    # Sólo el runner enlaza el contexto con la lease ya adquirida; no se acepta rebinding silencioso.
    def _bind_lease_authority(
        self,
        *,
        generation: int,
        checker: Callable[[], None],
    ) -> None:
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise TypeError('generation must be an int')
        if generation <= 0:
            raise ValueError('generation must be greater than zero')
        if not callable(checker):
            raise TypeError('checker must be callable')
        if self._lease_generation is not None or self._lease_authority_check is not None:
            raise RuntimeContractError('lease authority is already bound')
        self._lease_generation = generation
        self._lease_authority_check = checker

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


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_utc_datetime(value: datetime, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f'{name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f'{name} must be timezone-aware')
    if value.astimezone(UTC) != value:
        raise ValueError(f'{name} must use UTC timezone')
