# Espejo comentado del proceso NOTPII; la lógica coincide con producción.
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ada.processes.notpii.errors import NotPiiProcessConfigurationError
from atlanticus.configuration import (
    ConfigurationValueError,
    ConfigurationVariableSpec,
    ResolvedConfiguration,
)
from atlanticus.connectivity.service_bus import ServiceBusConfigurationError, ServiceBusSettings
from atlanticus.integrations.pi.contracts import PiExtractionMode

INTERPOLATED_SERVICE_BUS_PREFIX = 'NOTPII_INTERPOLATED_SERVICE_BUS'
RECORDED_SERVICE_BUS_PREFIX = 'NOTPII_RECORDED_SERVICE_BUS'
RAW_BATCH_SIZE_VARIABLE = 'NOTPII_RAW_BATCH_SIZE'
MAX_MESSAGE_COUNT_VARIABLE = 'NOTPII_MAX_MESSAGE_COUNT'

_MODE_PREFIXES = {
    PiExtractionMode.INTERPOLATED: INTERPOLATED_SERVICE_BUS_PREFIX,
    PiExtractionMode.RECORDED: RECORDED_SERVICE_BUS_PREFIX,
}
_MODE_ORDER = (PiExtractionMode.INTERPOLATED, PiExtractionMode.RECORDED)


@dataclass(frozen=True, slots=True)
class NotPiiSettings:
    service_buses: Mapping[PiExtractionMode, ServiceBusSettings]
    raw_batch_size: int
    max_message_count: int

    @classmethod
    def from_configuration(
        cls,
        configuration: ResolvedConfiguration,
        *,
        active_modes: Iterable[PiExtractionMode],
    ) -> NotPiiSettings:
        if not isinstance(configuration, ResolvedConfiguration):
            raise NotPiiProcessConfigurationError('configuration must be a ResolvedConfiguration')
        modes = _normalize_active_modes(active_modes)
        try:
            service_buses = {
                mode: _service_bus_settings(
                    configuration=configuration,
                    prefix=_MODE_PREFIXES[mode],
                )
                for mode in modes
            }
            raw_batch_size = _positive_int(configuration, RAW_BATCH_SIZE_VARIABLE)
            max_message_count = _positive_int(configuration, MAX_MESSAGE_COUNT_VARIABLE)
        except (ConfigurationValueError, ServiceBusConfigurationError) as error:
            raise NotPiiProcessConfigurationError(str(error)) from error
        return cls(
            service_buses=MappingProxyType(service_buses),
            raw_batch_size=raw_batch_size,
            max_message_count=max_message_count,
        )


def configuration_specs(
    *,
    active_modes: Iterable[PiExtractionMode],
) -> tuple[ConfigurationVariableSpec, ...]:
    modes = _normalize_active_modes(active_modes)
    service_bus_specs = tuple(
        spec for mode in modes for spec in _service_bus_specs(_MODE_PREFIXES[mode])
    )
    return (
        ConfigurationVariableSpec(key='APPLICATION'),
        ConfigurationVariableSpec(key='VOLUMEN_PATH'),
        *service_bus_specs,
        ConfigurationVariableSpec(key=RAW_BATCH_SIZE_VARIABLE, default='100000'),
        ConfigurationVariableSpec(key=MAX_MESSAGE_COUNT_VARIABLE, default='10'),
        ConfigurationVariableSpec(key='ATLANTICUS_AZURE_OBSERVABILITY_MODE', default='off'),
        ConfigurationVariableSpec(key='ATLANTICUS_AZURE_OBSERVABILITY_PROFILE', required=False),
        ConfigurationVariableSpec(
            key='APPLICATION_INSIGHTS_CONNECTION_STRING',
            required=False,
            sensitive=True,
        ),
    )


def _normalize_active_modes(values: Iterable[PiExtractionMode]) -> tuple[PiExtractionMode, ...]:
    if isinstance(values, PiExtractionMode | str | bytes):
        raise NotPiiProcessConfigurationError(
            'active_modes must be an iterable of extraction modes'
        )
    try:
        modes = tuple(values)
    except TypeError as error:
        raise NotPiiProcessConfigurationError(
            'active_modes must be an iterable of extraction modes'
        ) from error
    if not modes or any(not isinstance(mode, PiExtractionMode) for mode in modes):
        raise NotPiiProcessConfigurationError(
            'active_modes must contain at least one PiExtractionMode'
        )
    unique = set(modes)
    return tuple(mode for mode in _MODE_ORDER if mode in unique)


def _service_bus_settings(
    *,
    configuration: ResolvedConfiguration,
    prefix: str,
) -> ServiceBusSettings:
    return ServiceBusSettings(
        connection_string=configuration.require(f'{prefix}_CONNECTION_STRING'),
        topic_name=configuration.require(f'{prefix}_TOPIC_NAME'),
        subscription_name=configuration.require(f'{prefix}_SUBSCRIPTION_NAME'),
        max_wait_time_seconds=_positive_float(
            configuration,
            f'{prefix}_MAX_WAIT_TIME_SECONDS',
        ),
    )


def _service_bus_specs(prefix: str) -> tuple[ConfigurationVariableSpec, ...]:
    return (
        ConfigurationVariableSpec(
            key=f'{prefix}_CONNECTION_STRING',
            required=False,
            sensitive=True,
        ),
        ConfigurationVariableSpec(key=f'{prefix}_TOPIC_NAME', required=False),
        ConfigurationVariableSpec(key=f'{prefix}_SUBSCRIPTION_NAME', required=False),
        ConfigurationVariableSpec(key=f'{prefix}_MAX_WAIT_TIME_SECONDS', default='10'),
    )


def _positive_int(configuration: ResolvedConfiguration, key: str) -> int:
    value = configuration.get_int(key)
    if value is None or value <= 0:
        raise NotPiiProcessConfigurationError(f'{key} must be greater than zero')
    return value


def _positive_float(configuration: ResolvedConfiguration, key: str) -> float:
    raw = configuration.require(key)
    try:
        value = float(raw)
    except ValueError:
        raise NotPiiProcessConfigurationError(f'{key} must contain a positive number') from None
    if not math.isfinite(value) or value <= 0:
        raise NotPiiProcessConfigurationError(f'{key} must contain a positive number')
    return value
