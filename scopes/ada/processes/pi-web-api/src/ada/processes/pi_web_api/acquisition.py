from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ada.processes.pi_web_api.errors import (
    PiWebApiAcquisitionError,
    PiWebApiTimeoutExhaustedError,
)
from ada.processes.pi_web_api.models import (
    PiAcquisitionResult,
    PiAcquisitionWindow,
    PiExecutionPlan,
    PiSample,
    ResolvedPiTag,
)
from ada.processes.pi_web_api.timeout_retry import execute_with_timeout_retries
from atlanticus.integrations.pi.contracts import PiExtractionMode
from atlanticus.integrations.pi.web_api import (
    PiWebApiConnectionError,
    PiWebApiLimits,
    PiWebApiStatusError,
)
from atlanticus.runtime import JobRuntimeContext

_MIN_RECOVERY_WINDOW_SECONDS = 60
_RECOVERABLE_STATUS_CODES = frozenset({408, 425, 429})


class _StreamSetsResource(Protocol):
    def get_interpolated(
        self,
        web_ids: Iterable[str],
        *,
        start_time_utc: datetime,
        end_time_utc: datetime,
        interpolation_seconds: int,
    ) -> tuple[dict[str, Any], ...]: ...

    def get_recorded(
        self,
        web_ids: Iterable[str],
        *,
        start_time_utc: datetime,
        end_time_utc: datetime,
    ) -> tuple[dict[str, Any], ...]: ...


class _ClientSettings(Protocol):
    limits: PiWebApiLimits


class _PiWebApiClient(Protocol):
    settings: _ClientSettings
    streamsets: _StreamSetsResource


@dataclass(slots=True)
class _AcquisitionStats:
    interpolated_requests: int = 0
    recorded_requests: int = 0
    splits: int = 0
    interpolated_conflicts: int = 0
    recorded_conflicts: int = 0
    unexpected_records: int = 0


class PiStreamSetAcquirer:
    def __init__(self, *, client: _PiWebApiClient, max_data_points: int = 150_000) -> None:
        if not hasattr(client, 'streamsets') or not hasattr(client, 'settings'):
            raise TypeError('client must expose streamsets and settings')
        if (
            not isinstance(max_data_points, int)
            or isinstance(max_data_points, bool)
            or max_data_points <= 0
        ):
            raise ValueError('max_data_points must be a positive integer')
        self._client = client
        self._max_data_points = max_data_points

    def acquire(
        self,
        *,
        plan: PiExecutionPlan,
        window: PiAcquisitionWindow,
        context: JobRuntimeContext,
    ) -> PiAcquisitionResult:
        if not isinstance(plan, PiExecutionPlan):
            raise TypeError('plan must be a PiExecutionPlan')
        if not isinstance(window, PiAcquisitionWindow):
            raise TypeError('window must be a PiAcquisitionWindow')
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be a JobRuntimeContext')

        stats = _AcquisitionStats()
        interpolated = self._acquire_mode(
            tags=plan.interpolated,
            mode=PiExtractionMode.INTERPOLATED,
            window=window,
            context=context,
            stats=stats,
        )
        recorded = self._acquire_mode(
            tags=plan.recorded,
            mode=PiExtractionMode.RECORDED,
            window=window,
            context=context,
            stats=stats,
        )
        interpolated, interpolated_conflicts = _deduplicate_samples(interpolated)
        recorded, recorded_conflicts = _deduplicate_samples(recorded)
        stats.interpolated_conflicts += interpolated_conflicts
        stats.recorded_conflicts += recorded_conflicts
        if stats.interpolated_conflicts:
            context.logger.warning(
                'Conflicting interpolated PI samples were resolved by last received value',
                event_name='pi_web_api.interpolated.conflict',
                conflict_count=stats.interpolated_conflicts,
            )
        if stats.recorded_conflicts:
            context.logger.warning(
                'Conflicting recorded PI samples were resolved by last received value',
                event_name='pi_web_api.recorded.conflict',
                conflict_count=stats.recorded_conflicts,
            )
        if stats.unexpected_records:
            context.logger.warning(
                'Unexpected PI streamset records were ignored',
                event_name='pi_web_api.streamsets.unexpected_records',
                record_count=stats.unexpected_records,
            )
        return PiAcquisitionResult(
            interpolated=interpolated,
            recorded=recorded,
            interpolated_request_count=stats.interpolated_requests,
            recorded_request_count=stats.recorded_requests,
            split_count=stats.splits,
            interpolated_conflict_count=stats.interpolated_conflicts,
            recorded_conflict_count=stats.recorded_conflicts,
            unexpected_record_count=stats.unexpected_records,
        )

    def _acquire_mode(
        self,
        *,
        tags: tuple[ResolvedPiTag, ...],
        mode: PiExtractionMode,
        window: PiAcquisitionWindow,
        context: JobRuntimeContext,
        stats: _AcquisitionStats,
    ) -> tuple[PiSample, ...]:
        if not tags:
            return ()
        limit = (
            self._client.settings.limits.interpolated_max_web_ids
            if mode is PiExtractionMode.INTERPOLATED
            else self._client.settings.limits.recorded_max_web_ids
        )
        samples: list[PiSample] = []
        for offset in range(0, len(tags), limit):
            context.raise_if_cancelled()
            chunk = tags[offset : offset + limit]
            segments = (
                _segment_for_point_limit(
                    window=window,
                    tag_count=len(chunk),
                    max_data_points=self._max_data_points,
                )
                if mode is PiExtractionMode.INTERPOLATED
                else (window,)
            )
            for segment in segments:
                samples.extend(
                    self._fetch_with_recovery(
                        tags=chunk,
                        mode=mode,
                        window=segment,
                        context=context,
                        stats=stats,
                    )
                )
        return tuple(samples)

    def _fetch_with_recovery(
        self,
        *,
        tags: tuple[ResolvedPiTag, ...],
        mode: PiExtractionMode,
        window: PiAcquisitionWindow,
        context: JobRuntimeContext,
        stats: _AcquisitionStats,
    ) -> tuple[PiSample, ...]:
        context.raise_if_cancelled()
        try:
            records = self._fetch_with_timeout_retries(
                tags=tags,
                mode=mode,
                window=window,
                context=context,
                stats=stats,
            )
        except PiWebApiTimeoutExhaustedError:
            raise
        except Exception as error:
            if not _is_recoverable(error) or not _can_split(window):
                raise
            return self._split_and_retry(
                tags=tags,
                mode=mode,
                window=window,
                context=context,
                stats=stats,
                reason_type=type(error).__name__,
                reason_status_code=(
                    error.status_code if isinstance(error, PiWebApiStatusError) else None
                ),
            )
        if len(records) > self._max_data_points:
            if not _can_split(window):
                raise PiWebApiAcquisitionError(
                    'PI Web API response exceeds PI_WEB_API_MAX_DATA_POINTS at minimum window'
                )
            return self._split_and_retry(
                tags=tags,
                mode=mode,
                window=window,
                context=context,
                stats=stats,
                reason_type='response_too_large',
                response_point_count=len(records),
            )
        return _map_records(
            records=records,
            tags=tags,
            window=window,
            stats=stats,
        )

    def _fetch_with_timeout_retries(
        self,
        *,
        tags: tuple[ResolvedPiTag, ...],
        mode: PiExtractionMode,
        window: PiAcquisitionWindow,
        context: JobRuntimeContext,
        stats: _AcquisitionStats,
    ) -> tuple[dict[str, Any], ...]:
        try:
            records, _ = execute_with_timeout_retries(
                lambda: self._fetch(tags=tags, mode=mode, window=window, stats=stats),
                context=context,
                operation_name=f'streamsets.{mode.value}',
                attributes={
                    'extraction_mode': mode.value,
                    'first_slot_utc': window.first_slot_utc,
                    'last_slot_utc': window.last_slot_utc,
                },
            )
            return records
        except PiWebApiTimeoutExhaustedError as error:
            raise PiWebApiTimeoutExhaustedError(
                phase=error.phase,
                retry_count=error.retry_count,
                interpolated_request_count=stats.interpolated_requests,
                recorded_request_count=stats.recorded_requests,
                split_count=stats.splits,
            ) from None

    def _split_and_retry(
        self,
        *,
        tags: tuple[ResolvedPiTag, ...],
        mode: PiExtractionMode,
        window: PiAcquisitionWindow,
        context: JobRuntimeContext,
        stats: _AcquisitionStats,
        reason_type: str,
        reason_status_code: int | None = None,
        response_point_count: int | None = None,
    ) -> tuple[PiSample, ...]:
        left, right = _split_window(window)
        stats.splits += 1
        context.logger.warning(
            'PI window will be split and retried',
            event_name='pi_web_api.window.split',
            extraction_mode=mode.value,
            first_slot_utc=window.first_slot_utc,
            last_slot_utc=window.last_slot_utc,
            left_last_slot_utc=left.last_slot_utc,
            right_first_slot_utc=right.first_slot_utc,
            reason_type=reason_type,
            reason_status_code=reason_status_code,
            response_point_count=response_point_count,
            max_data_points=self._max_data_points,
        )
        return (
            *self._fetch_with_recovery(
                tags=tags,
                mode=mode,
                window=left,
                context=context,
                stats=stats,
            ),
            *self._fetch_with_recovery(
                tags=tags,
                mode=mode,
                window=right,
                context=context,
                stats=stats,
            ),
        )

    def _fetch(
        self,
        *,
        tags: tuple[ResolvedPiTag, ...],
        mode: PiExtractionMode,
        window: PiAcquisitionWindow,
        stats: _AcquisitionStats,
    ) -> tuple[dict[str, Any], ...]:
        web_ids = tuple(tag.web_id for tag in tags)
        end_time = window.last_slot_utc + timedelta(seconds=window.interpolation_seconds)
        if mode is PiExtractionMode.INTERPOLATED:
            stats.interpolated_requests += 1
            return self._client.streamsets.get_interpolated(
                web_ids,
                start_time_utc=window.first_slot_utc,
                end_time_utc=end_time,
                interpolation_seconds=window.interpolation_seconds,
            )
        stats.recorded_requests += 1
        return self._client.streamsets.get_recorded(
            web_ids,
            start_time_utc=window.first_slot_utc,
            end_time_utc=end_time,
        )


def _segment_for_point_limit(
    *,
    window: PiAcquisitionWindow,
    tag_count: int,
    max_data_points: int,
) -> tuple[PiAcquisitionWindow, ...]:
    if tag_count <= 0:
        return ()
    max_samples_per_tag = max_data_points // tag_count
    max_slots = max_samples_per_tag - 1
    if max_slots <= 0:
        raise PiWebApiAcquisitionError(
            'PI_WEB_API_MAX_DATA_POINTS is too low for one interpolated slot and tag chunk'
        )
    if window.slot_count <= max_slots:
        return (window,)
    segments: list[PiAcquisitionWindow] = []
    first = window.first_slot_utc
    remaining = window.slot_count
    while remaining:
        slot_count = min(max_slots, remaining)
        last = first + timedelta(seconds=(slot_count - 1) * window.interpolation_seconds)
        segments.append(
            PiAcquisitionWindow(
                first_slot_utc=first,
                last_slot_utc=last,
                interpolation_seconds=window.interpolation_seconds,
                recovery_truncated=window.recovery_truncated,
            )
        )
        remaining -= slot_count
        first = last + timedelta(seconds=window.interpolation_seconds)
    return tuple(segments)


def _can_split(window: PiAcquisitionWindow) -> bool:
    span_seconds = window.slot_count * window.interpolation_seconds
    return window.slot_count > 1 and span_seconds > _MIN_RECOVERY_WINDOW_SECONDS


def _split_window(
    window: PiAcquisitionWindow,
) -> tuple[PiAcquisitionWindow, PiAcquisitionWindow]:
    if window.slot_count < 2:
        raise PiWebApiAcquisitionError('PI acquisition window cannot be split further')
    left_count = window.slot_count // 2
    left_last = window.first_slot_utc + timedelta(
        seconds=(left_count - 1) * window.interpolation_seconds
    )
    right_first = left_last + timedelta(seconds=window.interpolation_seconds)
    return (
        PiAcquisitionWindow(
            first_slot_utc=window.first_slot_utc,
            last_slot_utc=left_last,
            interpolation_seconds=window.interpolation_seconds,
            recovery_truncated=window.recovery_truncated,
        ),
        PiAcquisitionWindow(
            first_slot_utc=right_first,
            last_slot_utc=window.last_slot_utc,
            interpolation_seconds=window.interpolation_seconds,
            recovery_truncated=window.recovery_truncated,
        ),
    )


def _is_recoverable(error: BaseException) -> bool:
    if isinstance(error, PiWebApiConnectionError):
        return True
    if isinstance(error, PiWebApiStatusError):
        return error.status_code in _RECOVERABLE_STATUS_CODES or error.status_code >= 500
    return False


def _map_records(
    *,
    records: tuple[dict[str, Any], ...],
    tags: tuple[ResolvedPiTag, ...],
    window: PiAcquisitionWindow,
    stats: _AcquisitionStats,
) -> tuple[PiSample, ...]:
    expected = {tag.tag_name.casefold(): tag.tag_name for tag in tags}
    end_exclusive = window.last_slot_utc + timedelta(seconds=window.interpolation_seconds)
    samples: list[PiSample] = []
    for record in records:
        if not isinstance(record, Mapping):
            stats.unexpected_records += 1
            continue
        name = record.get('name')
        timestamp = record.get('timestamp')
        if not isinstance(name, str) or name.casefold() not in expected:
            stats.unexpected_records += 1
            continue
        parsed = _parse_timestamp(timestamp)
        if parsed is None:
            stats.unexpected_records += 1
            continue
        if not window.first_slot_utc <= parsed < end_exclusive:
            continue
        samples.append(
            PiSample(
                tag_name=expected[name.casefold()],
                timestamp_utc=parsed,
                value=record.get('value'),
            )
        )
    return tuple(samples)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = value[:-1] + '+00:00' if value.endswith('Z') else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _deduplicate_samples(samples: tuple[PiSample, ...]) -> tuple[tuple[PiSample, ...], int]:
    selected: dict[tuple[str, datetime], PiSample] = {}
    conflicts = 0
    for sample in samples:
        key = (sample.tag_name.casefold(), sample.timestamp_utc)
        previous = selected.get(key)
        if previous is not None and previous.value != sample.value:
            conflicts += 1
        selected[key] = sample
    return tuple(selected.values()), conflicts
