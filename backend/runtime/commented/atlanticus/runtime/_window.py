# Construye una única ventana efectiva a partir de límites independientes.
# El modo relativo conserva el comportamiento histórico; el scheduled usa el slot cron como frontera adicional.
# Un platform timeout conocido puede truncar cualquiera de los dos modos, pero nunca obliga a consumir todo el tiempo.

"""Resolución de la ventana temporal efectiva de una invocación."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from atlanticus.runtime._schedule import ScheduleSlot, resolve_schedule_slot
from atlanticus.runtime.configuration import RuntimeConfiguration
from atlanticus.runtime.definition import JobDefinition


@dataclass(frozen=True, slots=True)
class ExecutionWindow:
    mode: str
    started_at_utc: datetime
    scheduled_at_utc: datetime | None
    next_scheduled_at_utc: datetime | None
    platform_deadline_utc: datetime | None
    deadline_utc: datetime
    safe_deadline_utc: datetime
    started_monotonic: float
    deadline_monotonic: float
    safe_deadline_monotonic: float

    def __post_init__(self) -> None:
        if self.mode not in {'relative', 'scheduled_external'}:
            raise ValueError('mode must be relative or scheduled_external')
        for name in (
            'started_at_utc',
            'deadline_utc',
            'safe_deadline_utc',
        ):
            _require_utc_datetime(getattr(self, name), name)
        for name in ('scheduled_at_utc', 'next_scheduled_at_utc', 'platform_deadline_utc'):
            value = getattr(self, name)
            if value is not None:
                _require_utc_datetime(value, name)
        for name in ('started_monotonic', 'deadline_monotonic', 'safe_deadline_monotonic'):
            _require_finite_number(getattr(self, name), name)
        if not self.started_monotonic <= self.safe_deadline_monotonic <= self.deadline_monotonic:
            raise ValueError('execution window monotonic deadlines must be ordered')
        if self.safe_deadline_utc > self.deadline_utc:
            raise ValueError('safe_deadline_utc must not exceed deadline_utc')
        if self.mode == 'relative':
            if self.scheduled_at_utc is not None or self.next_scheduled_at_utc is not None:
                raise ValueError('relative execution window must not contain schedule slots')
        else:
            if self.scheduled_at_utc is None or self.next_scheduled_at_utc is None:
                raise ValueError('scheduled execution window requires schedule slots')


def build_execution_window(
    *,
    definition: JobDefinition,
    configuration: RuntimeConfiguration,
    started_at_utc: datetime,
    started_monotonic: float,
) -> ExecutionWindow:
    if not isinstance(definition, JobDefinition):
        raise TypeError('definition must be a JobDefinition')
    if not isinstance(configuration, RuntimeConfiguration):
        raise TypeError('configuration must be a RuntimeConfiguration')
    normalized_started_at = _normalize_utc_datetime(started_at_utc, 'started_at_utc')
    _require_finite_number(started_monotonic, 'started_monotonic')

    schedule_slot = _resolve_schedule(configuration, normalized_started_at)
    if schedule_slot is None:
        mode = 'relative'
        runtime_deadline = normalized_started_at + timedelta(
            seconds=definition.execution_timeout_seconds
        )
    else:
        mode = 'scheduled_external'
        runtime_deadline = schedule_slot.scheduled_at_utc + timedelta(
            seconds=definition.execution_timeout_seconds
        )
        runtime_deadline = min(runtime_deadline, schedule_slot.next_scheduled_at_utc)

    platform_deadline = None
    if configuration.job_platform_timeout_seconds is not None:
        platform_deadline = normalized_started_at + timedelta(
            seconds=configuration.job_platform_timeout_seconds
        )
        runtime_deadline = min(runtime_deadline, platform_deadline)

    safe_deadline = runtime_deadline - timedelta(seconds=definition.shutdown_grace_seconds)
    deadline_budget = max(0.0, (runtime_deadline - normalized_started_at).total_seconds())
    safe_budget = max(0.0, (safe_deadline - normalized_started_at).total_seconds())

    return ExecutionWindow(
        mode=mode,
        started_at_utc=normalized_started_at,
        scheduled_at_utc=None if schedule_slot is None else schedule_slot.scheduled_at_utc,
        next_scheduled_at_utc=(
            None if schedule_slot is None else schedule_slot.next_scheduled_at_utc
        ),
        platform_deadline_utc=platform_deadline,
        deadline_utc=runtime_deadline,
        safe_deadline_utc=safe_deadline,
        started_monotonic=started_monotonic,
        deadline_monotonic=started_monotonic + deadline_budget,
        safe_deadline_monotonic=started_monotonic + min(safe_budget, deadline_budget),
    )


def _resolve_schedule(
    configuration: RuntimeConfiguration,
    started_at_utc: datetime,
) -> ScheduleSlot | None:
    if configuration.job_schedule_cron is None:
        return None
    return resolve_schedule_slot(
        expression=configuration.job_schedule_cron,
        timezone_name=configuration.job_schedule_timezone,
        now_utc=started_at_utc,
    )


def _normalize_utc_datetime(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f'{name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f'{name} must be timezone-aware')
    return value.astimezone(UTC)


def _require_utc_datetime(value: datetime, name: str) -> None:
    normalized = _normalize_utc_datetime(value, name)
    if normalized != value:
        raise ValueError(f'{name} must use UTC timezone')


def _require_finite_number(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f'{name} must be an int or float')
    if not math.isfinite(value):
        raise ValueError(f'{name} must be finite')
