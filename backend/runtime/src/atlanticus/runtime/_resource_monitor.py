"""Monitor interno de presión y estadísticas de recursos."""

from __future__ import annotations

import math
import threading
import time
from typing import Any

from atlanticus.kernel import OperationStatus
from atlanticus.observability import (
    ErrorInfo,
    EventCategory,
    EventSeverity,
    ObservabilityEvent,
    emit_event,
    get_execution_context,
)
from atlanticus.runtime._resource_models import (
    ResourceSample,
    ResourceSampler,
    ResourceStatistics,
    ResourceThresholds,
)
from atlanticus.runtime._resource_pressure import PressureDetector
from atlanticus.runtime._resource_sampler import CgroupResourceSampler


class ResourceMonitor:
    """Muestrea en memoria y sólo emite episodios sostenidos de presión o fallos."""

    def __init__(
        self,
        *,
        sampler: ResourceSampler | None = None,
        interval_seconds: float = 1.0,
        thresholds: ResourceThresholds | None = None,
        observe_cpu_pressure: bool = True,
    ) -> None:
        if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int | float):
            raise TypeError('interval_seconds must be an int or float')
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError('interval_seconds must be greater than zero')
        if sampler is not None and not callable(getattr(sampler, 'sample', None)):
            raise TypeError('sampler must provide a callable sample method')
        if thresholds is not None and not isinstance(thresholds, ResourceThresholds):
            raise TypeError('thresholds must be ResourceThresholds')
        if not isinstance(observe_cpu_pressure, bool):
            raise TypeError('observe_cpu_pressure must be a bool')
        self._sampler = sampler or CgroupResourceSampler()
        self._interval_seconds = interval_seconds
        if thresholds is None:
            sustained_samples = max(1, math.ceil(30 / interval_seconds))
            recovered_samples = max(1, math.ceil(10 / interval_seconds))
            self._thresholds = ResourceThresholds(
                warning_samples=sustained_samples,
                critical_samples=sustained_samples,
                emergency_samples=sustained_samples,
                recovered_samples=recovered_samples,
            )
        else:
            self._thresholds = thresholds
        self._observe_cpu_pressure = observe_cpu_pressure
        self._statistics = ResourceStatistics()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._context = get_execution_context()
        self._memory_detector = self._new_detector()
        self._cpu_detector = self._new_detector()
        self._pressure_event_count = 0
        self._sampling_failure_reported = False

    @property
    def statistics(self) -> ResourceStatistics:
        return self._statistics

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> ResourceMonitor:
        with self._lock:
            if self.running:
                raise RuntimeError('resource monitor is already running')
            self._context = get_execution_context()
            self._statistics = ResourceStatistics()
            self._memory_detector = self._new_detector()
            self._cpu_detector = self._new_detector()
            self._stop.clear()
            self._pressure_event_count = 0
            self._sampling_failure_reported = False
            self._thread = threading.Thread(
                target=self._run,
                name='atlanticus-resource-monitor',
                daemon=True,
            )
            self._thread.start()
        return self

    def checkpoint(self) -> ResourceSample | None:
        """Actualiza picos en memoria sin alterar la detección de presión periódica."""

        try:
            sample = self._sample_checkpoint()
        except Exception as error:
            self._report_sampling_failure(error)
            return None
        self._sampling_failure_reported = False
        return sample

    @property
    def pressure_event_count(self) -> int:
        return self._pressure_event_count

    def stop(self, *, timeout_seconds: float = 5.0) -> ResourceStatistics:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int | float):
            raise TypeError('timeout_seconds must be an int or float')
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError('timeout_seconds must be greater than zero')
        thread = self._thread
        if thread is None:
            return self._statistics
        self._stop.set()
        thread.join(timeout_seconds)
        if thread.is_alive():
            emit_event(
                ObservabilityEvent(
                    name='resource.monitor.stop_timed_out',
                    category=EventCategory.RESOURCE,
                    severity=EventSeverity.WARNING,
                    status=OperationStatus.WARNING,
                    context=self._context,
                    metrics={'timeout_seconds': timeout_seconds},
                )
            )
            return self._statistics
        self._thread = None
        self._memory_detector.finalize()
        if self._observe_cpu_pressure:
            self._cpu_detector.finalize()
        return self._statistics

    def __enter__(self) -> ResourceMonitor:
        return self.start()

    def __exit__(self, exc_type: Any, exc_value: Any, traceback_value: Any) -> None:
        self.stop()

    def _new_detector(self) -> PressureDetector:
        return PressureDetector(
            thresholds=self._thresholds,
            callback=self._emit_pressure_event,
            context=self._context,
        )

    def _emit_pressure_event(self, event: ObservabilityEvent) -> None:
        if event.name not in {'resource.pressure.recovered', 'resource.pressure.ongoing'}:
            self._pressure_event_count += 1
        emit_event(event)

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._sample_once(observe_cpu_pressure=True)
            except Exception as error:
                self._report_sampling_failure(error)
            else:
                self._sampling_failure_reported = False
            remaining = self._interval_seconds - (time.monotonic() - started)
            if remaining > 0:
                self._stop.wait(remaining)

    def _sample_once(self, *, observe_cpu_pressure: bool) -> ResourceSample:
        with self._lock:
            sample = self._sampler.sample()
            if not isinstance(sample, ResourceSample):
                raise TypeError('resource sampler must return ResourceSample')
            self._statistics.add(sample)
            self._memory_detector.observe('memory', sample.memory_percent, sample.occurred_at_utc)
            if self._observe_cpu_pressure and observe_cpu_pressure:
                self._cpu_detector.observe('cpu', sample.cpu_percent, sample.occurred_at_utc)
        return sample

    def _sample_checkpoint(self) -> ResourceSample:
        with self._lock:
            sample = self._sampler.sample()
            if not isinstance(sample, ResourceSample):
                raise TypeError('resource sampler must return ResourceSample')
            self._statistics.add(sample)
        return sample

    def _report_sampling_failure(self, error: Exception) -> None:
        if self._sampling_failure_reported:
            return
        try:
            emit_event(
                ObservabilityEvent(
                    name='resource.monitor.failed',
                    category=EventCategory.RESOURCE,
                    severity=EventSeverity.WARNING,
                    status=OperationStatus.WARNING,
                    context=self._context,
                    message='Resource sampling failed; job execution continues',
                    error=ErrorInfo.from_exception(error),
                )
            )
        except Exception:
            pass
        self._sampling_failure_reported = True
