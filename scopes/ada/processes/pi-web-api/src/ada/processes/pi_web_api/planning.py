from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ada.processes.pi_web_api.errors import PiWebApiPlannerError
from ada.processes.pi_web_api.models import PiAcquisitionWindow


@dataclass(frozen=True, slots=True)
class PiSlotPlanner:
    interpolation_seconds: int
    max_recovery_lookback_seconds: int = 3600
    max_recovery_window_seconds: int = 3600

    def __post_init__(self) -> None:
        for field_name in (
            'interpolation_seconds',
            'max_recovery_lookback_seconds',
            'max_recovery_window_seconds',
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise PiWebApiPlannerError(f'{field_name} must be a positive integer')
        for field_name in (
            'max_recovery_lookback_seconds',
            'max_recovery_window_seconds',
        ):
            value = getattr(self, field_name)
            if value < self.interpolation_seconds:
                raise PiWebApiPlannerError(
                    f'{field_name} must be greater than or equal to interpolation_seconds'
                )
            if value % self.interpolation_seconds != 0:
                raise PiWebApiPlannerError(
                    f'{field_name} must be divisible by interpolation_seconds'
                )
        if self.max_recovery_window_seconds > self.max_recovery_lookback_seconds:
            raise PiWebApiPlannerError(
                'max_recovery_window_seconds must not exceed max_recovery_lookback_seconds'
            )

    def floor_slot(self, value: datetime) -> datetime:
        normalized = _require_utc(value, field_name='value')
        epoch_seconds = math.floor(normalized.timestamp())
        aligned_seconds = epoch_seconds - (epoch_seconds % self.interpolation_seconds)
        return datetime.fromtimestamp(aligned_seconds, tz=UTC)

    def next_slot(self, value: datetime) -> datetime:
        normalized = _require_utc_second(value, field_name='value')
        if self.floor_slot(normalized) != normalized:
            raise PiWebApiPlannerError('value must be aligned to interpolation_seconds')
        return normalized + timedelta(seconds=self.interpolation_seconds)

    def next_wake_at(
        self,
        *,
        now_utc: datetime,
        committed_watermark_utc: datetime | None,
    ) -> datetime:
        now = _require_utc(now_utc, field_name='now_utc')
        if committed_watermark_utc is None:
            return now
        committed = _require_utc_second(
            committed_watermark_utc,
            field_name='committed_watermark_utc',
        )
        if self.floor_slot(committed) != committed:
            raise PiWebApiPlannerError(
                'committed_watermark_utc must be aligned to interpolation_seconds'
            )
        candidate = self.next_slot(committed)
        return now if candidate <= self.floor_slot(now) else candidate

    def plan(
        self,
        *,
        now_utc: datetime,
        committed_watermark_utc: datetime | None,
    ) -> PiAcquisitionWindow | None:
        target = self.floor_slot(now_utc)
        if committed_watermark_utc is None:
            return PiAcquisitionWindow(
                first_slot_utc=target,
                last_slot_utc=target,
                interpolation_seconds=self.interpolation_seconds,
            )

        committed = _require_utc_second(
            committed_watermark_utc,
            field_name='committed_watermark_utc',
        )
        if self.floor_slot(committed) != committed:
            raise PiWebApiPlannerError(
                'committed_watermark_utc must be aligned to interpolation_seconds'
            )
        first_pending = committed + timedelta(seconds=self.interpolation_seconds)
        if first_pending > target:
            return None

        lookback_slots = self.max_recovery_lookback_seconds // self.interpolation_seconds
        earliest_recoverable = target - timedelta(
            seconds=(lookback_slots - 1) * self.interpolation_seconds
        )
        recovery_truncated = first_pending < earliest_recoverable
        first_slot = max(first_pending, earliest_recoverable)

        window_slots = self.max_recovery_window_seconds // self.interpolation_seconds
        last_slot = min(
            target,
            first_slot + timedelta(seconds=(window_slots - 1) * self.interpolation_seconds),
        )
        return PiAcquisitionWindow(
            first_slot_utc=first_slot,
            last_slot_utc=last_slot,
            interpolation_seconds=self.interpolation_seconds,
            recovery_truncated=recovery_truncated,
        )


def _require_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PiWebApiPlannerError(f'{field_name} must be a timezone-aware datetime')
    if value.utcoffset() != timedelta(0):
        raise PiWebApiPlannerError(f'{field_name} must use UTC')
    return value.astimezone(UTC)


def _require_utc_second(value: datetime, *, field_name: str) -> datetime:
    normalized = _require_utc(value, field_name=field_name)
    if normalized.microsecond != 0:
        raise PiWebApiPlannerError(f'{field_name} must not contain microseconds')
    return normalized
