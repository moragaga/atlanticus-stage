"""Modelos neutrales que no exponen objetos de redis-py."""

from __future__ import annotations

from dataclasses import dataclass


# El modelo traduce los sentinels especiales de TTL a estados neutrales.
@dataclass(frozen=True, slots=True)
class RedisTtl:
    """Estado de existencia y expiración de una key Redis."""

    exists: bool
    seconds: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.exists, bool):
            raise ValueError('exists must be a boolean')
        if self.seconds is not None:
            if (
                not isinstance(self.seconds, int)
                or isinstance(self.seconds, bool)
                or self.seconds < 0
            ):
                raise ValueError('seconds must be a non-negative integer or None')
            if not self.exists:
                raise ValueError('seconds requires exists=True')

    @property
    def has_expiry(self) -> bool:
        return self.exists and self.seconds is not None
