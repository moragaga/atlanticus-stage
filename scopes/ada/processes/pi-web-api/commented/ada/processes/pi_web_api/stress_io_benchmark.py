# Benchmark pedagógico de la segunda etapa de presión PI Web API.
# Esta ruta NO materializa Parquet ni mueve watermarks: aisla únicamente el costo I/O + parsing JSON.
# La matriz mide dos ejes por separado:
# - ancho: 40/80/120/185 tags en una sola request, tanto para un slot como para una hora;
# - concurrencia: los 185 tags se parten en chunks y se comparan 1/2/3 workers.
# El benchmark usa el mismo PiWebApiClient síncrono abierto por la composición.
# Los threads solo ejecutan GET independientes; ningún thread escribe datasets, state ni métricas del runtime.
# Las métricas y logs se consolidan en el thread principal para conservar determinismo.
# Los timeouts se registran como caso de benchmark fallido y no se reintentan, para no contaminar la latencia medida.
# Ejecutar esta ruta con --run-once para que termine después de una sola matriz.
from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import fmean
from time import perf_counter
from typing import Any, Protocol

from ada.processes.pi_web_api.errors import PiWebApiProcessConfigurationError
from ada.processes.pi_web_api.job import PiWebApiJob
from ada.processes.pi_web_api.models import PiAcquisitionWindow, ResolvedPiTag
from atlanticus.integrations.pi.web_api import PiWebApiLimits, PiWebApiTimeoutError
from atlanticus.runtime import JobRuntimeContext

_IO_WIDTHS = (40, 80, 120, 185)
_IO_HOUR_SLOTS = 360


class _StreamSetsResource(Protocol):
    def get_interpolated(
        self,
        web_ids: Iterable[str],
        *,
        start_time_utc: datetime,
        end_time_utc: datetime,
        interpolation_seconds: int,
    ) -> tuple[dict[str, Any], ...]: ...


class _ClientSettings(Protocol):
    limits: PiWebApiLimits


class _PiWebApiClient(Protocol):
    settings: _ClientSettings
    streamsets: _StreamSetsResource


@dataclass(frozen=True, slots=True)
class PiStressIoChunkResult:
    index: int
    tag_count: int
    point_count: int
    duration_seconds: float
    timeout_phase: str | None = None

    @property
    def completed(self) -> bool:
        return self.timeout_phase is None


@dataclass(frozen=True, slots=True)
class PiStressIoCaseResult:
    axis: str
    window_kind: str
    tag_count: int
    slot_count: int
    chunk_limit: int
    chunk_count: int
    workers: int
    request_count: int
    point_count: int
    duration_seconds: float
    chunk_duration_min_seconds: float | None
    chunk_duration_mean_seconds: float | None
    chunk_duration_max_seconds: float | None
    timed_out_chunks: int

    @property
    def completed(self) -> bool:
        return self.timed_out_chunks == 0


@dataclass(slots=True)
class PiStressIoBenchmarkJob(PiWebApiJob):
    client: _PiWebApiClient
    benchmark_end_utc: datetime
    interpolation_seconds: int
    chunk_limit: int = 40
    max_workers: int = 3
    _completed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        PiWebApiJob.__post_init__(self)
        if not hasattr(self.client, 'streamsets') or not hasattr(self.client, 'settings'):
            raise TypeError('client must expose streamsets and settings')
        if not isinstance(self.benchmark_end_utc, datetime):
            raise TypeError('benchmark_end_utc must be a datetime')
        if self.benchmark_end_utc.tzinfo is None or self.benchmark_end_utc.utcoffset() != timedelta(0):
            raise PiWebApiProcessConfigurationError('benchmark_end_utc must use UTC')
        if self.benchmark_end_utc.microsecond:
            raise PiWebApiProcessConfigurationError('benchmark_end_utc must not contain microseconds')
        if not isinstance(self.interpolation_seconds, int) or isinstance(self.interpolation_seconds, bool):
            raise TypeError('interpolation_seconds must be an integer')
        if self.interpolation_seconds <= 0:
            raise PiWebApiProcessConfigurationError('interpolation_seconds must be greater than zero')
        if self.benchmark_end_utc.second % self.interpolation_seconds:
            raise PiWebApiProcessConfigurationError(
                'benchmark_end_utc must align to interpolation_seconds'
            )
        if not isinstance(self.chunk_limit, int) or isinstance(self.chunk_limit, bool):
            raise TypeError('chunk_limit must be an integer')
        if self.chunk_limit <= 0:
            raise PiWebApiProcessConfigurationError('chunk_limit must be greater than zero')
        if not isinstance(self.max_workers, int) or isinstance(self.max_workers, bool):
            raise TypeError('max_workers must be an integer')
        if not 1 <= self.max_workers <= 3:
            raise PiWebApiProcessConfigurationError('max_workers must be between 1 and 3')

    def run_iteration(self, context: JobRuntimeContext) -> None:
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be a JobRuntimeContext')
        if self._completed:
            context.set_iteration_fact('outcome', 'skipped')
            context.set_iteration_fact('reason', 'stress_io_complete')
            return

        preparation = self.prepare(context=context)
        tags = preparation.plan.interpolated
        if preparation.plan.recorded:
            raise PiWebApiProcessConfigurationError(
                'PI I/O stress benchmark requires an interpolated-only catalog'
            )
        if len(tags) < max(_IO_WIDTHS):
            raise PiWebApiProcessConfigurationError(
                'PI I/O stress benchmark requires at least 185 resolved interpolated tags'
            )
        integration_limit = self.client.settings.limits.interpolated_max_web_ids
        if integration_limit < max(_IO_WIDTHS):
            raise PiWebApiProcessConfigurationError(
                'PI_WEB_API_INTERPOLATED_MAX_WEB_IDS must be at least 185 for I/O benchmark'
            )
        if self.chunk_limit > integration_limit:
            raise PiWebApiProcessConfigurationError(
                'PI_WEB_API_STRESS_IO_CHUNK_LIMIT exceeds integration WebID limit'
            )

        context.mark_iteration_work()
        context.set_iteration_fact('stress_io_benchmark', True)
        context.set_iteration_fact('stress_io_end_utc', self.benchmark_end_utc)
        context.set_iteration_fact('stress_io_physical_tags', len(tags))
        context.set_iteration_fact('stress_io_chunk_limit', self.chunk_limit)
        context.set_iteration_fact('stress_io_max_workers', self.max_workers)
        context.set_iteration_fact('stress_io_case_count', 8 + (self.max_workers * 2))
        context.set_execution_fact('stress_io_benchmark', True)
        context.set_execution_fact('stress_io_physical_tags', len(tags))
        context.set_execution_fact('stress_io_chunk_limit', self.chunk_limit)
        context.set_execution_fact('stress_io_max_workers', self.max_workers)

        slot_window, hour_window = _build_io_windows(
            end_utc=self.benchmark_end_utc,
            interpolation_seconds=self.interpolation_seconds,
        )
        results: list[PiStressIoCaseResult] = []
        started = perf_counter()

        for tag_count in _IO_WIDTHS:
            selected = _select_evenly_spaced(tags, tag_count)
            for window_kind, window in (('slot', slot_window), ('hour', hour_window)):
                context.raise_if_cancelled()
                result = run_io_case(
                    client=self.client,
                    tags=selected,
                    window=window,
                    axis='width',
                    window_kind=window_kind,
                    chunk_limit=tag_count,
                    workers=1,
                )
                results.append(result)
                _log_case(context=context, result=result)

        for workers in range(1, self.max_workers + 1):
            for window_kind, window in (('slot', slot_window), ('hour', hour_window)):
                context.raise_if_cancelled()
                result = run_io_case(
                    client=self.client,
                    tags=tags,
                    window=window,
                    axis='concurrency',
                    window_kind=window_kind,
                    chunk_limit=self.chunk_limit,
                    workers=workers,
                )
                results.append(result)
                _log_case(context=context, result=result)

        duration = perf_counter() - started
        total_requests = sum(item.request_count for item in results)
        total_points = sum(item.point_count for item in results)
        timed_out_cases = sum(not item.completed for item in results)
        context.set_iteration_fact('outcome', 'completed')
        context.set_iteration_fact('stress_io_duration_seconds', round(duration, 6))
        context.set_iteration_fact('stress_io_requests', total_requests)
        context.set_iteration_fact('stress_io_points', total_points)
        context.set_iteration_fact('stress_io_timeout_cases', timed_out_cases)
        context.set_execution_fact('stress_io_duration_seconds', round(duration, 6))
        context.set_execution_fact('stress_io_requests', total_requests)
        context.set_execution_fact('stress_io_points', total_points)
        context.set_execution_fact('stress_io_timeout_cases', timed_out_cases)
        context.logger.info(
            'PI Web API I/O stress benchmark completed',
            event_name='pi_web_api.stress.io.completed',
            case_count=len(results),
            request_count=total_requests,
            point_count=total_points,
            timeout_cases=timed_out_cases,
            duration_seconds=round(duration, 6),
        )
        self._completed = True


def run_io_case(
    *,
    client: _PiWebApiClient,
    tags: tuple[ResolvedPiTag, ...],
    window: PiAcquisitionWindow,
    axis: str,
    window_kind: str,
    chunk_limit: int,
    workers: int,
) -> PiStressIoCaseResult:
    if not tags:
        raise PiWebApiProcessConfigurationError('I/O benchmark case requires tags')
    if chunk_limit <= 0:
        raise PiWebApiProcessConfigurationError('chunk_limit must be greater than zero')
    if workers <= 0:
        raise PiWebApiProcessConfigurationError('workers must be greater than zero')
    chunks = tuple(tags[offset : offset + chunk_limit] for offset in range(0, len(tags), chunk_limit))
    worker_count = min(workers, len(chunks))
    started = perf_counter()
    if worker_count == 1:
        chunk_results: list[PiStressIoChunkResult] = []
        for index, chunk in enumerate(chunks):
            result = _fetch_chunk(
                client=client,
                index=index,
                tags=chunk,
                window=window,
            )
            chunk_results.append(result)
            if not result.completed:
                break
    else:
        chunk_results = []
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix='pi-stress-io') as executor:
            futures = {
                executor.submit(
                    _fetch_chunk,
                    client=client,
                    index=index,
                    tags=chunk,
                    window=window,
                ): index
                for index, chunk in enumerate(chunks)
            }
            for future in as_completed(futures):
                chunk_results.append(future.result())
        chunk_results.sort(key=lambda item: item.index)
    duration = perf_counter() - started
    completed_durations = [item.duration_seconds for item in chunk_results if item.completed]
    return PiStressIoCaseResult(
        axis=axis,
        window_kind=window_kind,
        tag_count=len(tags),
        slot_count=window.slot_count,
        chunk_limit=chunk_limit,
        chunk_count=len(chunks),
        workers=worker_count,
        request_count=len(chunk_results),
        point_count=sum(item.point_count for item in chunk_results),
        duration_seconds=duration,
        chunk_duration_min_seconds=(min(completed_durations) if completed_durations else None),
        chunk_duration_mean_seconds=(fmean(completed_durations) if completed_durations else None),
        chunk_duration_max_seconds=(max(completed_durations) if completed_durations else None),
        timed_out_chunks=sum(not item.completed for item in chunk_results),
    )


def _fetch_chunk(
    *,
    client: _PiWebApiClient,
    index: int,
    tags: tuple[ResolvedPiTag, ...],
    window: PiAcquisitionWindow,
) -> PiStressIoChunkResult:
    started = perf_counter()
    try:
        records = client.streamsets.get_interpolated(
            tuple(item.web_id for item in tags),
            start_time_utc=window.first_slot_utc,
            end_time_utc=window.last_slot_utc
            + timedelta(seconds=window.interpolation_seconds),
            interpolation_seconds=window.interpolation_seconds,
        )
    except PiWebApiTimeoutError as error:
        return PiStressIoChunkResult(
            index=index,
            tag_count=len(tags),
            point_count=0,
            duration_seconds=perf_counter() - started,
            timeout_phase=error.phase,
        )
    return PiStressIoChunkResult(
        index=index,
        tag_count=len(tags),
        point_count=len(records),
        duration_seconds=perf_counter() - started,
    )


def _build_io_windows(
    *,
    end_utc: datetime,
    interpolation_seconds: int,
) -> tuple[PiAcquisitionWindow, PiAcquisitionWindow]:
    slot_window = PiAcquisitionWindow(
        first_slot_utc=end_utc,
        last_slot_utc=end_utc,
        interpolation_seconds=interpolation_seconds,
        recovery_truncated=False,
    )
    hour_window = PiAcquisitionWindow(
        first_slot_utc=end_utc
        - timedelta(seconds=(_IO_HOUR_SLOTS - 1) * interpolation_seconds),
        last_slot_utc=end_utc,
        interpolation_seconds=interpolation_seconds,
        recovery_truncated=False,
    )
    return slot_window, hour_window


def _select_evenly_spaced(
    tags: tuple[ResolvedPiTag, ...],
    count: int,
) -> tuple[ResolvedPiTag, ...]:
    if count <= 0 or count > len(tags):
        raise PiWebApiProcessConfigurationError('tag sample count is outside available range')
    if count == len(tags):
        return tags
    if count == 1:
        return (tags[0],)
    last_index = len(tags) - 1
    indexes = tuple(round(index * last_index / (count - 1)) for index in range(count))
    if len(set(indexes)) != count:
        raise PiWebApiProcessConfigurationError('could not build a unique stress tag sample')
    return tuple(tags[index] for index in indexes)


def _log_case(*, context: JobRuntimeContext, result: PiStressIoCaseResult) -> None:
    context.logger.info(
        'PI Web API I/O stress benchmark case completed',
        event_name='pi_web_api.stress.io.case',
        axis=result.axis,
        window_kind=result.window_kind,
        tag_count=result.tag_count,
        slot_count=result.slot_count,
        chunk_limit=result.chunk_limit,
        chunk_count=result.chunk_count,
        workers=result.workers,
        request_count=result.request_count,
        point_count=result.point_count,
        duration_seconds=round(result.duration_seconds, 6),
        chunk_duration_min_seconds=_rounded_optional(result.chunk_duration_min_seconds),
        chunk_duration_mean_seconds=_rounded_optional(result.chunk_duration_mean_seconds),
        chunk_duration_max_seconds=_rounded_optional(result.chunk_duration_max_seconds),
        timed_out_chunks=result.timed_out_chunks,
        outcome='completed' if result.completed else 'timeout',
    )


def _rounded_optional(value: float | None) -> float | None:
    return None if value is None else round(value, 6)
