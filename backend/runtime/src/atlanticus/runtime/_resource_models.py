"""Modelos y acumuladores internos de recursos."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ResourceSample:
    """Lectura puntual utilizada en memoria y descartada tras actualizar estadísticas."""

    occurred_at_utc: datetime
    memory_used_bytes: int | None = None
    memory_limit_bytes: int | None = None
    memory_percent: float | None = None
    cpu_percent: float | None = None
    cpu_limit_cores: float | None = None
    process_rss_bytes: int | None = None
    process_count: int = 0
    thread_count: int = 0
    oom_count: int | None = None
    cpu_throttled_periods: int | None = None
    cpu_throttled_seconds: float | None = None
    top_process_rss_bytes: int | None = None
    memory_source: str = 'unknown'
    cpu_source: str = 'unknown'

    def __post_init__(self) -> None:
        if not isinstance(self.occurred_at_utc, datetime):
            raise TypeError('occurred_at_utc must be a datetime')
        if self.occurred_at_utc.tzinfo is None:
            raise ValueError('occurred_at_utc must be timezone-aware')
        for name in (
            'memory_used_bytes',
            'memory_limit_bytes',
            'process_rss_bytes',
            'oom_count',
            'cpu_throttled_periods',
            'top_process_rss_bytes',
        ):
            _validate_optional_non_negative_int(getattr(self, name), name)
        for name in ('process_count', 'thread_count'):
            _validate_non_negative_int(getattr(self, name), name)
        for name in (
            'memory_percent',
            'cpu_percent',
            'cpu_limit_cores',
            'cpu_throttled_seconds',
        ):
            _validate_optional_non_negative_number(getattr(self, name), name)
        for name in ('memory_source', 'cpu_source'):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f'{name} must be a string')
            if not value.strip():
                raise ValueError(f'{name} must not be empty')
        if self.memory_percent is not None and self.memory_percent > 100:
            raise ValueError('memory_percent must not exceed 100')


class ResourceSampler(Protocol):
    """Contrato inyectable para probar el monitor sin depender del host."""

    def sample(self) -> ResourceSample:
        """Retorna una lectura puntual."""


@dataclass(frozen=True, slots=True)
class ResourceThresholds:
    """Umbrales porcentuales y persistencia mínima de cada transición."""

    warning_percent: float = 85.0
    critical_percent: float = 92.0
    emergency_percent: float = 97.0
    recovered_percent: float = 80.0
    warning_samples: int = 3
    critical_samples: int = 2
    emergency_samples: int = 1
    recovered_samples: int = 5
    ongoing_event_seconds: float = 30.0

    def __post_init__(self) -> None:
        for name in (
            'warning_percent',
            'critical_percent',
            'emergency_percent',
            'recovered_percent',
            'ongoing_event_seconds',
        ):
            _validate_non_negative_number(getattr(self, name), name)
        if not (
            0
            <= self.recovered_percent
            < self.warning_percent
            < self.critical_percent
            < self.emergency_percent
            <= 100
        ):
            raise ValueError('resource percentages must be ordered between zero and 100')
        for name in (
            'warning_samples',
            'critical_samples',
            'emergency_samples',
            'recovered_samples',
        ):
            _validate_non_negative_int(getattr(self, name), name)
            if getattr(self, name) <= 0:
                raise ValueError(f'{name} must be greater than zero')
        if self.ongoing_event_seconds <= 0:
            raise ValueError('ongoing_event_seconds must be greater than zero')


@dataclass(slots=True)
class _Aggregate:
    count: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None

    def add(self, value: int | float | None) -> None:
        if value is None:
            return
        _validate_non_negative_number(value, 'aggregate value')
        numeric = float(value)
        self.count += 1
        self.total += numeric
        self.minimum = numeric if self.minimum is None else min(self.minimum, numeric)
        self.maximum = numeric if self.maximum is None else max(self.maximum, numeric)

    def to_metrics(self, prefix: str) -> dict[str, int | float]:
        if self.count == 0:
            return {}
        assert self.minimum is not None
        assert self.maximum is not None
        return {
            f'{prefix}_sample_count': self.count,
            f'{prefix}_min': round(self.minimum, 3),
            f'{prefix}_avg': round(self.total / self.count, 3),
            f'{prefix}_max': round(self.maximum, 3),
        }


@dataclass(slots=True)
class ResourceStatistics:
    """Acumuladores constantes en memoria; no conserva la serie de muestras."""

    sample_count: int = 0
    first_sample_at_utc: datetime | None = None
    last_sample_at_utc: datetime | None = None
    memory_percent: _Aggregate = field(default_factory=_Aggregate)
    memory_used_bytes: _Aggregate = field(default_factory=_Aggregate)
    cpu_percent: _Aggregate = field(default_factory=_Aggregate)
    process_rss_bytes: _Aggregate = field(default_factory=_Aggregate)
    process_count: _Aggregate = field(default_factory=_Aggregate)
    thread_count: _Aggregate = field(default_factory=_Aggregate)
    top_process_rss_bytes: _Aggregate = field(default_factory=_Aggregate)
    peak_sample: ResourceSample | None = None
    last_sample: ResourceSample | None = None
    initial_oom_count: int | None = None
    last_oom_count: int | None = None
    initial_cpu_throttled_periods: int | None = None
    last_cpu_throttled_periods: int | None = None
    initial_cpu_throttled_seconds: float | None = None
    last_cpu_throttled_seconds: float | None = None

    def add(self, sample: ResourceSample) -> None:
        if not isinstance(sample, ResourceSample):
            raise TypeError('sample must be a ResourceSample')
        self.sample_count += 1
        self.first_sample_at_utc = self.first_sample_at_utc or sample.occurred_at_utc
        self.last_sample_at_utc = sample.occurred_at_utc
        self.memory_percent.add(sample.memory_percent)
        self.memory_used_bytes.add(sample.memory_used_bytes)
        self.cpu_percent.add(sample.cpu_percent)
        self.process_rss_bytes.add(sample.process_rss_bytes)
        self.process_count.add(sample.process_count)
        self.thread_count.add(sample.thread_count)
        self.top_process_rss_bytes.add(sample.top_process_rss_bytes)
        self.last_sample = sample
        if self.initial_oom_count is None and sample.oom_count is not None:
            self.initial_oom_count = sample.oom_count
        if sample.oom_count is not None:
            self.last_oom_count = sample.oom_count
        if self.initial_cpu_throttled_periods is None and sample.cpu_throttled_periods is not None:
            self.initial_cpu_throttled_periods = sample.cpu_throttled_periods
        if sample.cpu_throttled_periods is not None:
            self.last_cpu_throttled_periods = sample.cpu_throttled_periods
        if self.initial_cpu_throttled_seconds is None and sample.cpu_throttled_seconds is not None:
            self.initial_cpu_throttled_seconds = sample.cpu_throttled_seconds
        if sample.cpu_throttled_seconds is not None:
            self.last_cpu_throttled_seconds = sample.cpu_throttled_seconds
        if sample.memory_percent is not None and (
            self.peak_sample is None
            or self.peak_sample.memory_percent is None
            or sample.memory_percent > self.peak_sample.memory_percent
        ):
            self.peak_sample = sample

    def metrics(self) -> dict[str, int | float]:
        values: dict[str, int | float] = {'resource_sample_count': self.sample_count}
        reference_sample = self.peak_sample or self.last_sample
        if reference_sample is not None:
            if reference_sample.memory_limit_bytes is not None:
                values['memory_limit_bytes'] = reference_sample.memory_limit_bytes
            if reference_sample.cpu_limit_cores is not None:
                values['cpu_limit_cores'] = reference_sample.cpu_limit_cores
        oom_events = self._counter_delta(self.initial_oom_count, self.last_oom_count)
        if oom_events is not None:
            values['oom_events'] = oom_events
        throttled_periods = self._counter_delta(
            self.initial_cpu_throttled_periods,
            self.last_cpu_throttled_periods,
        )
        if throttled_periods is not None:
            values['cpu_throttled_periods'] = throttled_periods
        throttled_seconds = self._counter_delta(
            self.initial_cpu_throttled_seconds,
            self.last_cpu_throttled_seconds,
        )
        if throttled_seconds is not None:
            values['cpu_throttled_seconds'] = round(throttled_seconds, 6)
        values.update(self.memory_percent.to_metrics('memory_percent'))
        values.update(self.memory_used_bytes.to_metrics('memory_used_bytes'))
        values.update(self.cpu_percent.to_metrics('cpu_percent'))
        values.update(self.process_rss_bytes.to_metrics('process_rss_bytes'))
        if self.process_count.maximum is not None:
            values['process_count_max'] = int(self.process_count.maximum)
        if self.thread_count.maximum is not None:
            values['thread_count_max'] = int(self.thread_count.maximum)
        if self.top_process_rss_bytes.maximum is not None:
            values['peak_top_process_rss_bytes'] = int(self.top_process_rss_bytes.maximum)
        return values

    def attributes(self) -> dict[str, Any]:
        peak = self.peak_sample
        reference_sample = peak or self.last_sample
        if reference_sample is None:
            return {}
        return {
            key: value
            for key, value in {
                'first_sample_at_utc': self.first_sample_at_utc,
                'last_sample_at_utc': self.last_sample_at_utc,
                'memory_peak_at_utc': None if peak is None else peak.occurred_at_utc,
                'memory_source': reference_sample.memory_source,
                'cpu_source': reference_sample.cpu_source,
            }.items()
            if value is not None
        }

    def operational_metrics(self) -> dict[str, int | float]:
        values: dict[str, int | float] = {}
        reference_sample = self.peak_sample or self.last_sample
        if reference_sample is not None:
            if reference_sample.cpu_limit_cores is not None:
                values['cpu_limit_cores'] = round(reference_sample.cpu_limit_cores, 3)
            if reference_sample.memory_limit_bytes is not None:
                values['memory_limit_bytes'] = reference_sample.memory_limit_bytes
        if self.cpu_percent.maximum is not None:
            values['cpu_peak_percent'] = round(self.cpu_percent.maximum, 3)
        if self.memory_percent.maximum is not None:
            values['memory_peak_percent'] = round(self.memory_percent.maximum, 3)
        oom_events = self._counter_delta(self.initial_oom_count, self.last_oom_count)
        if oom_events is not None and oom_events > 0:
            values['oom_events'] = int(oom_events)
        throttled_seconds = self._counter_delta(
            self.initial_cpu_throttled_seconds,
            self.last_cpu_throttled_seconds,
        )
        if throttled_seconds is not None and throttled_seconds > 0:
            values['cpu_throttled_seconds'] = round(float(throttled_seconds), 6)
        return values

    @staticmethod
    def _counter_delta(
        initial: int | float | None,
        current: int | float | None,
    ) -> int | float | None:
        if initial is None or current is None:
            return None
        return max(0, current - initial)


def _validate_non_negative_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f'{name} must be an int')
    if value < 0:
        raise ValueError(f'{name} must be greater than or equal to zero')


def _validate_optional_non_negative_int(value: int | None, name: str) -> None:
    if value is not None:
        _validate_non_negative_int(value, name)


def _validate_non_negative_number(value: int | float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f'{name} must be an int or float')
    if not math.isfinite(value) or value < 0:
        raise ValueError(f'{name} must be finite and greater than or equal to zero')


def _validate_optional_non_negative_number(value: int | float | None, name: str) -> None:
    if value is not None:
        _validate_non_negative_number(value, name)
