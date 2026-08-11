"""Acumuladores pequeños para hechos operacionales de jobs e iteraciones."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import TypeAlias

OperationalValue: TypeAlias = str | bool | int | float | date | datetime | Enum | Path

_KEY_PATTERN = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
_SENSITIVE_KEY_PARTS = (
    'password',
    'passwd',
    'pwd',
    'secret',
    'token',
    'credential',
    'connection_string',
    'access_key',
    'api_key',
)
_RESERVED_KEYS = frozenset(
    {
        'application',
        'cause_message',
        'cause_type',
        'cpu_limit_cores',
        'cpu_peak_percent',
        'cpu_throttled_seconds',
        'diagnostic_available',
        'diagnostic_ref',
        'duration_seconds',
        'empty_iterations',
        'environment',
        'error_code',
        'error_message',
        'error_type',
        'event',
        'iteration',
        'iterations',
        'level',
        'memory_limit_bytes',
        'memory_peak_percent',
        'message',
        'oom_events',
        'resource_pressure_events',
        'retryable',
        'run_id',
        'service',
        'status',
        'stop_reason',
        'time',
        'work_iterations',
    }
)
_MAX_FIELDS = 32


class OperationalSummary:
    """Mantiene valores escalares en memoria sin emitir telemetría por actualización."""

    def __init__(self) -> None:
        self._values: dict[str, OperationalValue] = {}

    def set(self, key: str, value: OperationalValue) -> None:
        normalized = self._validate_key(key)
        self._validate_value(normalized, value)
        if normalized not in self._values and len(self._values) >= _MAX_FIELDS:
            raise ValueError(f'operational summary supports at most {_MAX_FIELDS} fields')
        self._values[normalized] = value

    def increment(self, key: str, amount: int | float = 1) -> None:
        normalized = self._validate_key(key)
        if isinstance(amount, bool) or not isinstance(amount, int | float):
            raise TypeError('operational counter amount must be an int or float')
        if isinstance(amount, float) and not math.isfinite(amount):
            raise ValueError('operational counter amount must be finite')
        current = self._values.get(normalized, 0)
        if isinstance(current, bool) or not isinstance(current, int | float):
            raise TypeError(f'operational field {normalized!r} is not a counter')
        self.set(normalized, current + amount)

    def get(self, key: str, default: OperationalValue | None = None) -> OperationalValue | None:
        normalized = self._validate_key(key)
        return self._values.get(normalized, default)

    def snapshot(self) -> dict[str, OperationalValue]:
        return dict(self._values)

    def clear(self) -> None:
        self._values.clear()

    @staticmethod
    def _validate_key(key: str) -> str:
        normalized = key.strip()
        if not _KEY_PATTERN.fullmatch(normalized):
            raise ValueError('operational summary keys must use lower snake_case')
        if normalized in _RESERVED_KEYS:
            raise ValueError(f'operational summary key is reserved: {normalized}')
        lowered = normalized.lower()
        if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
            raise ValueError(f'operational summary key is sensitive: {normalized}')
        return normalized

    @staticmethod
    def _validate_value(key: str, value: OperationalValue) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f'operational field {key!r} must be finite')
        if not isinstance(value, str | bool | int | float | date | datetime | Enum | Path):
            raise TypeError(f'operational field {key!r} must be a scalar value')
        if isinstance(value, str) and len(value) > 500:
            raise ValueError(f'operational field {key!r} must not exceed 500 characters')
