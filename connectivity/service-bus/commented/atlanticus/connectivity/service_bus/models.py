"""Modelos neutrales para recepción y settlement de Service Bus."""
# Espejo pedagógico: conserva exactamente el contrato ejecutable y agrega contexto de diseño.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any


# Solo se modelan estados posibles bajo el contrato fijo PeekLock.
class ServiceBusDeliveryState(StrEnum):
    """Estado local conocido para una entrega PeekLock."""

    ACTIVE = 'active'
    COMPLETED = 'completed'
    ABANDONED = 'abandoned'
    DEAD_LETTERED = 'dead_lettered'


@dataclass(frozen=True, slots=True)
class ServiceBusMessage:
    """Cuerpo opaco y metadatos técnicos de una entrega individual."""

    body: bytes = field(repr=False)
    message_id: str | None = None
    correlation_id: str | None = None
    subject: str | None = None
    content_type: str | None = None
    enqueued_time_utc: datetime | None = None
    sequence_number: int | None = None
    delivery_count: int | None = None
    application_properties: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, 'body', bytes(self.body))
        object.__setattr__(
            self,
            'application_properties',
            MappingProxyType(dict(self.application_properties)),
        )

    def decode_text(self, *, encoding: str = 'utf-8') -> str:
        """Decodifica el cuerpo únicamente cuando el consumidor lo solicita."""

        return self.body.decode(encoding)
