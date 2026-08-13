"""Configuración inmutable y explícita para una suscripción Service Bus."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from atlanticus.connectivity.service_bus.errors import ServiceBusConfigurationError

DEFAULT_SERVICE_BUS_MAX_WAIT_TIME_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class ServiceBusSettings:
    """Credencial, tópico, suscripción y espera ya resueltos por composición."""

    connection_string: str = field(repr=False)
    topic_name: str
    subscription_name: str
    max_wait_time_seconds: float = DEFAULT_SERVICE_BUS_MAX_WAIT_TIME_SECONDS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            'connection_string',
            _require_secret(self.connection_string, 'connection_string'),
        )
        object.__setattr__(self, 'topic_name', _require_entity_name(self.topic_name, 'topic_name'))
        object.__setattr__(
            self,
            'subscription_name',
            _require_entity_name(self.subscription_name, 'subscription_name'),
        )
        object.__setattr__(
            self,
            'max_wait_time_seconds',
            _require_positive_number(self.max_wait_time_seconds, 'max_wait_time_seconds'),
        )


def _require_secret(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ServiceBusConfigurationError(f'{field_name} must be text')
    if value == '':
        raise ServiceBusConfigurationError(f'{field_name} is required')
    if '\x00' in value:
        raise ServiceBusConfigurationError(f'{field_name} must not contain null characters')
    return value


def _require_entity_name(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ServiceBusConfigurationError(f'{field_name} must be text')
    if not value or not value.strip():
        raise ServiceBusConfigurationError(f'{field_name} is required')
    if value != value.strip():
        raise ServiceBusConfigurationError(f'{field_name} must not contain surrounding whitespace')
    if '\x00' in value or '\r' in value or '\n' in value:
        raise ServiceBusConfigurationError(f'{field_name} contains unsupported characters')
    return value


def _require_positive_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ServiceBusConfigurationError(f'{field_name} must be a positive number')
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ServiceBusConfigurationError(f'{field_name} must be a positive number')
    return normalized
