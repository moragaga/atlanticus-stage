# Representa el timestamp UTC canónico heredado del watermark PI una vez que el cálculo KPI hizo commit.
# Se conserva precisión de segundos y el mismo valor sirve para trazabilidad entre procesos.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True, order=True)
class KpiWatermark:
    timestamp_utc: datetime

    def __post_init__(self) -> None:
        value = _normalize_utc_second(self.timestamp_utc)
        object.__setattr__(self, 'timestamp_utc', value)

    @property
    def text(self) -> str:
        return self.timestamp_utc.strftime('%Y-%m-%dT%H:%M:%SZ')

    @property
    def filename_token(self) -> str:
        return self.timestamp_utc.strftime('%Y%m%dT%H%M%SZ')

    def as_document(self) -> dict[str, str]:
        return {'watermark_utc': self.text}

    @classmethod
    def parse(cls, value: str) -> KpiWatermark:
        if not isinstance(value, str) or not value.strip():
            raise ValueError('watermark must be a non-empty ISO-8601 string')
        normalized = value.strip()
        try:
            parsed = datetime.fromisoformat(normalized.replace('Z', '+00:00'))
        except ValueError as error:
            raise ValueError('watermark must be a valid ISO-8601 datetime') from error
        return cls(parsed)

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> KpiWatermark:
        if not isinstance(document, Mapping):
            raise TypeError('watermark document must be a mapping')
        value = document.get('watermark_utc')
        if not isinstance(value, str):
            raise ValueError('watermark document requires watermark_utc')
        return cls.parse(value)


def _normalize_utc_second(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError('watermark timestamp must be a datetime')
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('watermark timestamp must be timezone-aware')
    normalized = value.astimezone(UTC)
    if normalized.microsecond != 0:
        raise ValueError('watermark timestamp must use second precision')
    return normalized
