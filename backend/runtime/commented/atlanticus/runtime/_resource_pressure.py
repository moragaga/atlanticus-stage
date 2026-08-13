# El detector exige muestras consecutivas antes de abrir o escalar un episodio.
# El código bajo estos comentarios es equivalente al productivo y conserva el mismo comportamiento.

"""Detección interna de episodios sostenidos de presión."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from atlanticus.kernel import OperationStatus, utc_now
from atlanticus.observability import (
    EventCategory,
    EventSeverity,
    ExecutionContext,
    ObservabilityEvent,
)
from atlanticus.runtime._resource_models import ResourceThresholds


@dataclass(slots=True)
class _PressureEpisode:
    resource: str
    level: str
    began_at_utc: datetime
    began_monotonic: float
    peak_percent: float
    peak_at_utc: datetime
    last_ongoing_monotonic: float


class PressureDetector:
    """Convierte varias muestras en intervalos de presión relevantes."""

    _LEVEL_ORDER = {'normal': 0, 'warning': 1, 'critical': 2, 'emergency': 3}

    def __init__(
        self,
        *,
        thresholds: ResourceThresholds,
        callback: Callable[[ObservabilityEvent], None],
        context: ExecutionContext,
    ) -> None:
        self._thresholds = thresholds
        self._callback = callback
        self._context = context
        self._candidate_level = 'normal'
        self._candidate_count = 0
        self._candidate_began_at_utc: datetime | None = None
        self._candidate_began_monotonic: float | None = None
        self._candidate_peak_percent: float | None = None
        self._recovered_count = 0
        self._episode: _PressureEpisode | None = None

    def observe(self, resource: str, percent: float | None, occurred_at: datetime) -> None:
        if percent is None:
            return
        now = time.monotonic()
        requested = self._requested_level(percent)
        if self._episode is None:
            self._observe_candidate(resource, requested, percent, occurred_at, now)
            return

        episode = self._episode
        if percent > episode.peak_percent:
            episode.peak_percent = percent
            episode.peak_at_utc = occurred_at

        if percent < self._thresholds.recovered_percent:
            self._recovered_count += 1
            if self._recovered_count >= self._thresholds.recovered_samples:
                self._emit_recovered(episode, percent, occurred_at, now)
                self._episode = None
                self._candidate_level = 'normal'
                self._candidate_count = 0
                self._candidate_began_at_utc = None
                self._candidate_began_monotonic = None
                self._candidate_peak_percent = None
                self._recovered_count = 0
            return
        self._recovered_count = 0

        if self._LEVEL_ORDER[requested] > self._LEVEL_ORDER[episode.level]:
            if requested == self._candidate_level:
                self._candidate_count += 1
            else:
                self._candidate_level = requested
                self._candidate_count = 1
            if self._candidate_count >= self._required_samples(requested):
                previous = episode.level
                episode.level = requested
                self._candidate_count = 0
                self._emit_pressure(
                    'resource.pressure.escalated', episode, percent, occurred_at, now, previous
                )
        else:
            self._candidate_level = 'normal'
            self._candidate_count = 0
            if now - episode.last_ongoing_monotonic >= self._thresholds.ongoing_event_seconds:
                self._emit_pressure('resource.pressure.ongoing', episode, percent, occurred_at, now)

    def finalize(self, occurred_at: datetime | None = None) -> None:
        if self._episode is None:
            return
        ended_at = occurred_at or utc_now()
        now = time.monotonic()
        self._emit_pressure(
            'resource.pressure.open_at_monitor_stop',
            self._episode,
            self._episode.peak_percent,
            ended_at,
            now,
        )

    def _observe_candidate(
        self,
        resource: str,
        level: str,
        percent: float,
        occurred_at: datetime,
        now: float,
    ) -> None:
        if level == 'normal':
            self._candidate_level = 'normal'
            self._candidate_count = 0
            self._candidate_began_at_utc = None
            self._candidate_began_monotonic = None
            self._candidate_peak_percent = None
            return
        if level == self._candidate_level:
            self._candidate_count += 1
            self._candidate_peak_percent = max(self._candidate_peak_percent or percent, percent)
        else:
            self._candidate_level = level
            self._candidate_count = 1
            self._candidate_began_at_utc = occurred_at
            self._candidate_began_monotonic = now
            self._candidate_peak_percent = percent
        if self._candidate_count < self._required_samples(level):
            return
        began_at = self._candidate_began_at_utc or occurred_at
        began_monotonic = self._candidate_began_monotonic or now
        self._episode = _PressureEpisode(
            resource=resource,
            level=level,
            began_at_utc=began_at,
            began_monotonic=began_monotonic,
            peak_percent=self._candidate_peak_percent or percent,
            peak_at_utc=occurred_at,
            last_ongoing_monotonic=now,
        )
        self._candidate_count = 0
        self._candidate_began_at_utc = None
        self._candidate_began_monotonic = None
        self._candidate_peak_percent = None
        self._emit_pressure('resource.pressure.started', self._episode, percent, occurred_at, now)

    def _requested_level(self, percent: float) -> str:
        if percent >= self._thresholds.emergency_percent:
            return 'emergency'
        if percent >= self._thresholds.critical_percent:
            return 'critical'
        if percent >= self._thresholds.warning_percent:
            return 'warning'
        return 'normal'

    def _required_samples(self, level: str) -> int:
        return {
            'warning': self._thresholds.warning_samples,
            'critical': self._thresholds.critical_samples,
            'emergency': self._thresholds.emergency_samples,
        }.get(level, 1)

    def _emit_pressure(
        self,
        name: str,
        episode: _PressureEpisode,
        current_percent: float,
        occurred_at: datetime,
        now: float,
        previous_level: str | None = None,
    ) -> None:
        episode.last_ongoing_monotonic = now
        severity = {
            'warning': EventSeverity.WARNING,
            'critical': EventSeverity.ERROR,
            'emergency': EventSeverity.CRITICAL,
        }[episode.level]
        messages = {
            'resource.pressure.started': 'Sustained resource pressure detected',
            'resource.pressure.escalated': 'Sustained resource pressure escalated',
            'resource.pressure.ongoing': 'Sustained resource pressure remains active',
            'resource.pressure.open_at_monitor_stop': (
                'Resource pressure remained active when monitoring stopped'
            ),
        }
        self._callback(
            ObservabilityEvent(
                name=name,
                category=EventCategory.RESOURCE,
                severity=severity,
                message=messages.get(name),
                status=(
                    OperationStatus.WARNING if episode.level == 'warning' else OperationStatus.ERROR
                ),
                context=self._context,
                occurred_at_utc=occurred_at,
                duration_ms=max(0.0, (now - episode.began_monotonic) * 1000),
                metrics={
                    f'{episode.resource}_current_percent': current_percent,
                    f'{episode.resource}_peak_percent': episode.peak_percent,
                },
                attributes={
                    'resource': episode.resource,
                    'level': episode.level,
                    'previous_level': previous_level,
                    'began_at_utc': episode.began_at_utc,
                    'peak_at_utc': episode.peak_at_utc,
                },
            )
        )

    def _emit_recovered(
        self,
        episode: _PressureEpisode,
        current_percent: float,
        occurred_at: datetime,
        now: float,
    ) -> None:
        self._callback(
            ObservabilityEvent(
                name='resource.pressure.recovered',
                category=EventCategory.RESOURCE,
                severity=EventSeverity.INFO,
                message='Resource pressure recovered',
                status=OperationStatus.SUCCESS,
                context=self._context,
                occurred_at_utc=occurred_at,
                duration_ms=max(0.0, (now - episode.began_monotonic) * 1000),
                metrics={
                    f'{episode.resource}_current_percent': current_percent,
                    f'{episode.resource}_peak_percent': episode.peak_percent,
                },
                attributes={
                    'resource': episode.resource,
                    'highest_level': episode.level,
                    'began_at_utc': episode.began_at_utc,
                    'peak_at_utc': episode.peak_at_utc,
                    'ended_at_utc': occurred_at,
                },
            )
        )
