"""Conversión de valores operacionales en datos acotados y seguros para JSON."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

DEFAULT_SENSITIVE_KEY_PARTS = (
    'password',
    'passwd',
    'pwd',
    'secret',
    'token',
    'credential',
    'connection_string',
    'access_key',
    'api_key',
    'account_key',
    'authorization',
    'sas',
    'shared_access_signature',
)

REDACTED = '***redacted***'


def _normalize_sensitive_key(value: str) -> str:
    return ''.join(character for character in value.casefold() if character.isalnum())


class DataSanitizer:
    """Prepara valores acotados para logs, eventos y diagnósticos.

    El sanitizador no cifra datos ni reemplaza la seguridad de la aplicación. Su objetivo es reducir
    la exposición accidental de secretos y evitar payloads de diagnóstico demasiado grandes.
    """

    def __init__(
        self,
        *,
        max_depth: int = 4,
        max_items: int = 50,
        max_string_length: int = 500,
        sensitive_key_parts: tuple[str, ...] = DEFAULT_SENSITIVE_KEY_PARTS,
    ) -> None:
        if max_depth < 0:
            raise ValueError('max_depth must be greater than or equal to zero.')
        if max_items < 1:
            raise ValueError('max_items must be greater than zero.')
        if max_string_length < 1:
            raise ValueError('max_string_length must be greater than zero.')
        if not sensitive_key_parts:
            raise ValueError('sensitive_key_parts must not be empty.')

        normalized_sensitive_key_parts: list[str] = []
        for part in sensitive_key_parts:
            if not isinstance(part, str) or not part.strip():
                raise ValueError('sensitive_key_parts must contain only non-empty strings.')
            normalized_part = _normalize_sensitive_key(part)
            if not normalized_part:
                raise ValueError('sensitive_key_parts must contain only non-empty strings.')
            normalized_sensitive_key_parts.append(normalized_part)

        self.max_depth = max_depth
        self.max_items = max_items
        self.max_string_length = max_string_length
        self.sensitive_key_parts = tuple(normalized_sensitive_key_parts)

    def sanitize(self, value: Any, *, key: str | None = None) -> Any:
        """Retorna una representación de ``value`` segura para JSON."""

        return self._sanitize(value, key=key, depth=self.max_depth)

    def is_sensitive_key(self, key: str) -> bool:
        """Indica si el nombre de un campo parece sensible."""

        normalized = _normalize_sensitive_key(key)
        return any(part in normalized for part in self.sensitive_key_parts)

    def _sanitize(self, value: Any, *, key: str | None, depth: int) -> Any:
        if key is not None and self.is_sensitive_key(key):
            return REDACTED

        if value is None or isinstance(value, bool | int):
            return value

        if isinstance(value, float):
            return self._sanitize_float(value)

        if isinstance(value, str):
            return self._truncate(value)

        if isinstance(value, datetime | date | time):
            return value.isoformat()

        if isinstance(value, timedelta):
            return value.total_seconds()

        if isinstance(value, Decimal | UUID | Path):
            return str(value)

        if isinstance(value, Enum):
            return self._sanitize(value.value, key=key, depth=depth)

        if isinstance(value, bytes | bytearray | memoryview):
            return {
                'type': type(value).__name__,
                'size_bytes': len(value),
            }

        if isinstance(value, BaseException):
            return {'type': type(value).__name__}

        if depth < 0:
            return {
                'type': type(value).__name__,
                'summary': 'max_depth_reached',
            }

        if is_dataclass(value) and not isinstance(value, type):
            return self._sanitize_dataclass(value, depth=depth)

        if isinstance(value, Mapping):
            return self._sanitize_mapping(value, depth=depth)

        if isinstance(value, list | tuple | set | frozenset):
            return self._sanitize_collection(value, depth=depth)

        return {
            'type': type(value).__name__,
            'summary': 'unsupported_object',
        }

    def _sanitize_dataclass(self, value: Any, *, depth: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for index, field in enumerate(fields(value)):
            if index >= self.max_items:
                result['__truncated__'] = True
                break
            result[field.name] = self._sanitize(
                getattr(value, field.name),
                key=field.name,
                depth=depth - 1,
            )
        return result

    def _sanitize_mapping(self, value: Mapping[Any, Any], *, depth: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for index, (item_key, item_value) in enumerate(value.items()):
            if index >= self.max_items:
                result['__truncated__'] = True
                break
            safe_key = str(item_key)
            result[safe_key] = self._sanitize(
                item_value,
                key=safe_key,
                depth=depth - 1,
            )
        return result

    def _sanitize_collection(
        self,
        value: list[Any] | tuple[Any, ...] | set[Any] | frozenset[Any],
        *,
        depth: int,
    ) -> list[Any]:
        values = list(value)
        result = [
            self._sanitize(item, key=None, depth=depth - 1) for item in values[: self.max_items]
        ]
        if len(values) > self.max_items:
            result.append({'__truncated__': True})
        return result

    def _sanitize_float(self, value: float) -> float | str:
        if math.isfinite(value):
            return value
        if math.isnan(value):
            return 'NaN'
        if value > 0:
            return 'Infinity'
        return '-Infinity'

    def _truncate(self, value: str) -> str:
        if len(value) <= self.max_string_length:
            return value
        return f'{value[: self.max_string_length]}...<truncated>'
