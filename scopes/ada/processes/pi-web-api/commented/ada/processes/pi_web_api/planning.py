from __future__ import annotations

# Planificación del eje temporal UTC. Esta capa no consulta PI ni escribe datasets.

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ada.processes.pi_web_api.errors import PiWebApiPlannerError
from ada.processes.pi_web_api.models import PiAcquisitionWindow


@dataclass(frozen=True, slots=True)
# El planner usa un único eje de slots cerrados; no crea un eje diferente para recorded.
class PiSlotPlanner:
    interpolation_seconds: int
    max_recovery_seconds: int = 3600

    def __post_init__(self) -> None:
        for field_name in ('interpolation_seconds', 'max_recovery_seconds'):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise PiWebApiPlannerError(f'{field_name} must be a positive integer')
        if self.max_recovery_seconds < self.interpolation_seconds:
            raise PiWebApiPlannerError(
                'max_recovery_seconds must be greater than or equal to interpolation_seconds'
            )
        if self.max_recovery_seconds % self.interpolation_seconds != 0:
            raise PiWebApiPlannerError(
                'max_recovery_seconds must be divisible by interpolation_seconds'
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

    # Si todavía no existe un slot nuevo devuelve None y evita una llamada innecesaria a PI.
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

        total_slots = (
            int((target - first_pending).total_seconds()) // self.interpolation_seconds
        ) + 1
        max_slots = self.max_recovery_seconds // self.interpolation_seconds
        recovery_truncated = total_slots > max_slots
        first_slot = (
            target - timedelta(seconds=(max_slots - 1) * self.interpolation_seconds)
            if recovery_truncated
            else first_pending
        )
        return PiAcquisitionWindow(
            first_slot_utc=first_slot,
            last_slot_utc=target,
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
