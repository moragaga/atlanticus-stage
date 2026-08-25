# Normaliza la política común de retry SQL usando un prefijo definido por cada proceso.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_DEFAULT_RETRY_ATTEMPTS = 10
_DEFAULT_RETRY_DELAY_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class SqlRetryPolicy:
    attempts: int = _DEFAULT_RETRY_ATTEMPTS
    delay_seconds: float = _DEFAULT_RETRY_DELAY_SECONDS

    def __post_init__(self) -> None:
        if (
            not isinstance(self.attempts, int)
            or isinstance(self.attempts, bool)
            or self.attempts <= 0
        ):
            raise ValueError('attempts must be an integer greater than zero')
        if isinstance(self.delay_seconds, bool):
            raise ValueError('delay_seconds must be a number greater than or equal to zero')
        try:
            delay_seconds = float(self.delay_seconds)
        except TypeError, ValueError:
            raise ValueError(
                'delay_seconds must be a number greater than or equal to zero'
            ) from None
        if delay_seconds < 0:
            raise ValueError('delay_seconds must be a number greater than or equal to zero')
        object.__setattr__(self, 'delay_seconds', delay_seconds)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any], *, prefix: str) -> SqlRetryPolicy:
        normalized_prefix = _required_text(prefix, 'prefix').upper()
        attempts_key = f'{normalized_prefix}_SQL_RETRY_ATTEMPTS'
        delay_key = f'{normalized_prefix}_SQL_RETRY_DELAY_SECONDS'
        return cls(
            attempts=_parse_positive_integer(
                values.get(attempts_key),
                default=_DEFAULT_RETRY_ATTEMPTS,
                field_name=attempts_key,
            ),
            delay_seconds=_parse_non_negative_number(
                values.get(delay_key),
                default=_DEFAULT_RETRY_DELAY_SECONDS,
                field_name=delay_key,
            ),
        )


def _parse_positive_integer(value: Any, *, default: int, field_name: str) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise ValueError(f'{field_name} must be an integer greater than zero')
    try:
        parsed = int(value)
    except TypeError, ValueError:
        raise ValueError(f'{field_name} must be an integer greater than zero') from None
    if parsed <= 0 or str(value).strip() not in {str(parsed), f'+{parsed}'}:
        raise ValueError(f'{field_name} must be an integer greater than zero')
    return parsed


def _parse_non_negative_number(value: Any, *, default: float, field_name: str) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise ValueError(f'{field_name} must be a number greater than or equal to zero')
    try:
        parsed = float(value)
    except TypeError, ValueError:
        raise ValueError(f'{field_name} must be a number greater than or equal to zero') from None
    if parsed < 0:
        raise ValueError(f'{field_name} must be a number greater than or equal to zero')
    return parsed


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value.strip()
