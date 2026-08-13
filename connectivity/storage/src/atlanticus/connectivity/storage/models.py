"""Modelos neutrales que no exponen objetos del SDK Azure Storage."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class StorageBlobProperties:
    """Propiedades estables y suficientes de un blob."""

    name: str
    size: int
    etag: str | None = None
    last_modified: datetime | None = None
    content_type: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = str(self.name)
        if not name:
            raise ValueError('name must not be empty')
        if not isinstance(self.size, int) or isinstance(self.size, bool) or self.size < 0:
            raise ValueError('size must be a non-negative integer')
        metadata = {str(key): str(value) for key, value in dict(self.metadata).items()}
        object.__setattr__(self, 'name', name)
        object.__setattr__(self, 'metadata', MappingProxyType(metadata))
