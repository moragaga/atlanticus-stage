from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PiPointWebIdResult:
    tag_name: str
    path: str
    point_name: str | None
    web_id: str | None
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'tag_name', _require_text(self.tag_name, 'tag_name'))
        object.__setattr__(self, 'path', _require_text(self.path, 'path'))
        object.__setattr__(self, 'point_name', _optional_text(self.point_name, 'point_name'))
        object.__setattr__(self, 'web_id', _optional_text(self.web_id, 'web_id'))
        object.__setattr__(self, 'error', _optional_text(self.error, 'error'))
        if (self.web_id is None) == (self.error is None):
            raise ValueError('exactly one of web_id or error must be provided')

    @property
    def resolved(self) -> bool:
        return self.web_id is not None


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f'{field_name} must be text')
    normalized = value.strip()
    if not normalized:
        raise ValueError(f'{field_name} must be non-empty text')
    return normalized


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f'{field_name} must be text or null')
    normalized = value.strip()
    return normalized or None
