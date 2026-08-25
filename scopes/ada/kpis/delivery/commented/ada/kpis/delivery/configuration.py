# Contrato puro de entrega KPI: configuración, Latest y Series sin infraestructura.
# Lee y valida la proyección KPI que queda congelada durante el job.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ada.kpis.delivery.errors import KpiDeliveryValidationError

# Constante interna o contractual centralizada para evitar literales dispersos.
KPI_CONFIGURATION_ID = 'kpis'
# Constante interna o contractual centralizada para evitar literales dispersos.
KPI_CONFIGURATION_PARTITION_ID = 'kpis'
# Constante interna o contractual centralizada para evitar literales dispersos.
KPI_CONFIGURATION_DOCUMENT_TYPE = 'ada_kpi_configuration_projection'
# Constante interna o contractual centralizada para evitar literales dispersos.
KPI_CONFIGURATION_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiDeliveryConfigurationBinding:
    key: str
    destination_keys: tuple[str, ...]
    latest_enabled: bool
    series_enabled: bool
    series_hours: int | None

    def __post_init__(self) -> None:
        key = _required_text(self.key, 'key')
        destinations = _destination_keys(self.destination_keys)
        if not isinstance(self.latest_enabled, bool):
            raise KpiDeliveryValidationError('latest_enabled must be boolean')
        if not isinstance(self.series_enabled, bool):
            raise KpiDeliveryValidationError('series_enabled must be boolean')
        if self.series_enabled:
            if (
                not isinstance(self.series_hours, int)
                or isinstance(self.series_hours, bool)
                or not 1 <= self.series_hours <= 24
            ):
                raise KpiDeliveryValidationError(
                    'series_hours must be an integer from 1 to 24 when series is enabled'
                )
        elif self.series_hours is not None:
            raise KpiDeliveryValidationError('series_hours must be null when series is disabled')
        object.__setattr__(self, 'key', key)
        object.__setattr__(self, 'destination_keys', destinations)


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiDeliveryConfiguration:
    revision: str
    tool_projection_revision: str
    bindings: tuple[KpiDeliveryConfigurationBinding, ...]

    def __post_init__(self) -> None:
        revision = _required_text(self.revision, 'revision')
        tool_projection_revision = _required_text(
            self.tool_projection_revision,
            'tool_projection_revision',
        )
        bindings = tuple(self.bindings)
        if not all(isinstance(item, KpiDeliveryConfigurationBinding) for item in bindings):
            raise KpiDeliveryValidationError(
                'bindings must contain KpiDeliveryConfigurationBinding values'
            )
        keys = tuple(item.key for item in bindings)
        if len(keys) != len(set(keys)):
            raise KpiDeliveryValidationError('KPI delivery configuration keys must be unique')
        object.__setattr__(self, 'revision', revision)
        object.__setattr__(self, 'tool_projection_revision', tool_projection_revision)
        object.__setattr__(self, 'bindings', bindings)

    @property
    def latest_bindings(self) -> tuple[KpiDeliveryConfigurationBinding, ...]:
        return tuple(item for item in self.bindings if item.latest_enabled)

    @property
    def series_bindings(self) -> tuple[KpiDeliveryConfigurationBinding, ...]:
        return tuple(item for item in self.bindings if item.series_enabled)

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> KpiDeliveryConfiguration:
        if not isinstance(document, Mapping):
            raise KpiDeliveryValidationError('KPI delivery configuration must be a mapping')
        if document.get('id') != KPI_CONFIGURATION_ID:
            raise KpiDeliveryValidationError('KPI delivery configuration id is invalid')
        if document.get('partition_key') != KPI_CONFIGURATION_PARTITION_ID:
            raise KpiDeliveryValidationError('KPI delivery configuration partition_key is invalid')
        if document.get('document_type') != KPI_CONFIGURATION_DOCUMENT_TYPE:
            raise KpiDeliveryValidationError('KPI delivery configuration document_type is invalid')
        if document.get('schema_version') != KPI_CONFIGURATION_SCHEMA_VERSION:
            raise KpiDeliveryValidationError('KPI delivery configuration schema_version is invalid')
        configuration = document.get('configuration')
        if not isinstance(configuration, Mapping):
            raise KpiDeliveryValidationError('KPI delivery configuration payload must be an object')
        raw_bindings = configuration.get('bindings')
        if not isinstance(raw_bindings, list):
            raise KpiDeliveryValidationError('KPI delivery configuration bindings must be an array')
        bindings = tuple(
            _binding_from_payload(item, index) for index, item in enumerate(raw_bindings)
        )
        return cls(
            revision=_required_text(document.get('revision'), 'revision'),
            tool_projection_revision=_required_text(
                document.get('tool_projection_revision'),
                'tool_projection_revision',
            ),
            bindings=bindings,
        )

    def destinations_for_latest(self) -> Mapping[str, tuple[str, ...]]:
        destinations: dict[str, list[str]] = {}
        for binding in self.latest_bindings:
            for destination in binding.destination_keys:
                destinations.setdefault(destination, []).append(binding.key)
        return MappingProxyType(
            {key: tuple(values) for key, values in sorted(destinations.items())}
        )

    def destinations_for_series(self) -> Mapping[str, tuple[str, ...]]:
        destinations: dict[str, list[str]] = {}
        for binding in self.series_bindings:
            for destination in binding.destination_keys:
                destinations.setdefault(destination, []).append(binding.key)
        return MappingProxyType(
            {key: tuple(values) for key, values in sorted(destinations.items())}
        )


# La función mantiene una operación pequeña y verificable de esta frontera.
def _binding_from_payload(
    payload: object,
    index: int,
) -> KpiDeliveryConfigurationBinding:
    if not isinstance(payload, Mapping):
        raise KpiDeliveryValidationError(
            f'KPI delivery configuration binding {index} must be an object'
        )
    destinations = payload.get('destination_keys')
    if not isinstance(destinations, list):
        raise KpiDeliveryValidationError(
            f'KPI delivery configuration binding {index} destination_keys must be an array'
        )
    return KpiDeliveryConfigurationBinding(
        key=_required_text(payload.get('key'), f'bindings[{index}].key'),
        destination_keys=tuple(destinations),
        latest_enabled=_required_bool(
            payload.get('latest_enabled'),
            f'bindings[{index}].latest_enabled',
        ),
        series_enabled=_required_bool(
            payload.get('series_enabled'),
            f'bindings[{index}].series_enabled',
        ),
        series_hours=payload.get('series_hours'),
    )


# La función mantiene una operación pequeña y verificable de esta frontera.
def _destination_keys(values: object) -> tuple[str, ...]:
    if isinstance(values, str | bytes):
        raise KpiDeliveryValidationError('destination_keys must be an iterable of strings')
    try:
        resolved = tuple(_required_text(value, 'destination_key') for value in values)
    except TypeError as error:
        raise KpiDeliveryValidationError(
            'destination_keys must be an iterable of strings'
        ) from error
    if not resolved:
        raise KpiDeliveryValidationError('destination_keys must not be empty')
    if len(resolved) != len(set(resolved)):
        raise KpiDeliveryValidationError('destination_keys must not contain duplicates')
    return resolved


# La función mantiene una operación pequeña y verificable de esta frontera.
def _required_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise KpiDeliveryValidationError(f'{field_name} must be boolean')
    return value


# La función mantiene una operación pequeña y verificable de esta frontera.
def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise KpiDeliveryValidationError(f'{field_name} must be a non-empty string')
    if value != value.strip():
        raise KpiDeliveryValidationError(f'{field_name} must not contain surrounding whitespace')
    return value
