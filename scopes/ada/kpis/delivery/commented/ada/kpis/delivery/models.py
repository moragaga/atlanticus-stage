# Contrato puro de entrega KPI: configuración, Latest y Series sin infraestructura.
# Contiene modelos inmutables y validaciones del contrato.

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType

from ada.kpis.core import KpiValueKind, KpiWatermark
from ada.kpis.delivery.errors import KpiDeliveryValidationError


# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiDeliveryStatus(StrEnum):
    OK = 'ok'
    ERROR = 'error'
    MISSING = 'missing'


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiDeliveryValue:
    status: KpiDeliveryStatus
    value_kind: KpiValueKind | None
    value: object = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, KpiDeliveryStatus):
            raise KpiDeliveryValidationError('status must be KpiDeliveryStatus')
        if self.value_kind is not None and not isinstance(self.value_kind, KpiValueKind):
            raise KpiDeliveryValidationError('value_kind must be KpiValueKind or None')
        if self.status is KpiDeliveryStatus.MISSING:
            if self.value_kind is not None or self.value is not None:
                raise KpiDeliveryValidationError(
                    'missing value must not contain value_kind or value'
                )
            return
        if self.value_kind is None:
            raise KpiDeliveryValidationError('resolved value requires value_kind')
        if self.status is KpiDeliveryStatus.ERROR:
            if self.value is not None:
                raise KpiDeliveryValidationError('error value must not contain value')
            return
        _validate_json_value(self.value, 'value')

    def as_payload(self) -> dict[str, object]:
        return {
            'status': self.status.value,
            'value_kind': None if self.value_kind is None else self.value_kind.value,
            'value': self.value,
        }


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiDeliveryManifest:
    schema_version: int
    watermark: KpiWatermark | None
    configuration_revision: str
    tool_projection_revision: str
    published_at_utc: datetime
    revision: str

    def __post_init__(self) -> None:
        _positive_integer(self.schema_version, 'schema_version')
        if self.watermark is not None and not isinstance(self.watermark, KpiWatermark):
            raise KpiDeliveryValidationError('watermark must be KpiWatermark or None')
        object.__setattr__(
            self,
            'configuration_revision',
            _required_text(self.configuration_revision, 'configuration_revision'),
        )
        object.__setattr__(
            self,
            'tool_projection_revision',
            _required_text(self.tool_projection_revision, 'tool_projection_revision'),
        )
        object.__setattr__(
            self,
            'published_at_utc',
            _utc_datetime(self.published_at_utc, 'published_at_utc'),
        )
        object.__setattr__(self, 'revision', _required_text(self.revision, 'revision'))

    def as_payload(self) -> dict[str, object]:
        return {
            'schema_version': self.schema_version,
            'watermark_utc': None if self.watermark is None else self.watermark.text,
            'configuration_revision': self.configuration_revision,
            'tool_projection_revision': self.tool_projection_revision,
            'published_at_utc': _format_utc(self.published_at_utc),
            'revision': self.revision,
        }


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiDeliverySnapshot:
    id: str
    partition_id: str
    document_type: str
    manifest: KpiDeliveryManifest
    destinations: Mapping[str, Mapping[str, KpiDeliveryValue]]

    def __post_init__(self) -> None:
        object.__setattr__(self, 'id', _required_text(self.id, 'id'))
        object.__setattr__(self, 'partition_id', _required_text(self.partition_id, 'partition_id'))
        object.__setattr__(
            self, 'document_type', _required_text(self.document_type, 'document_type')
        )
        if not isinstance(self.manifest, KpiDeliveryManifest):
            raise KpiDeliveryValidationError('manifest must be KpiDeliveryManifest')
        object.__setattr__(self, 'destinations', _normalize_destinations(self.destinations))

    def as_document(self) -> dict[str, object]:
        return {
            'id': self.id,
            'partition_id': self.partition_id,
            'document_type': self.document_type,
            'manifest': self.manifest.as_payload(),
            'destinations': {
                destination: {key: value.as_payload() for key, value in values.items()}
                for destination, values in self.destinations.items()
            },
        }


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesPoint:
    timestamp_utc: datetime
    key: str
    value: object = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            'timestamp_utc',
            _utc_datetime(self.timestamp_utc, 'timestamp_utc'),
        )
        object.__setattr__(self, 'key', _required_text(self.key, 'key'))
        _validate_series_value(self.value)


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesWindow:
    hours: int
    start_utc: datetime
    keys: tuple[str, ...]
    values: tuple[tuple[object, ...], ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.hours, int)
            or isinstance(self.hours, bool)
            or not 1 <= self.hours <= 24
        ):
            raise KpiDeliveryValidationError('hours must be an integer from 1 to 24')
        object.__setattr__(self, 'start_utc', _utc_datetime(self.start_utc, 'start_utc'))
        keys = tuple(_required_text(value, 'key') for value in self.keys)
        if not keys:
            raise KpiDeliveryValidationError('keys must not be empty')
        if len(keys) != len(set(keys)):
            raise KpiDeliveryValidationError('keys must not contain duplicates')
        rows = tuple(tuple(row) for row in self.values)
        if len(rows) != len(keys):
            raise KpiDeliveryValidationError('values row count must match keys')
        lengths = {len(row) for row in rows}
        if len(lengths) > 1:
            raise KpiDeliveryValidationError('all timeseries rows must have the same length')
        for row in rows:
            for value in row:
                _validate_series_value(value)
        object.__setattr__(self, 'keys', keys)
        object.__setattr__(self, 'values', rows)


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesManifest:
    schema_version: int
    watermark: KpiWatermark
    configuration_revision: str
    tool_projection_revision: str
    published_at_utc: datetime
    revision: str

    def __post_init__(self) -> None:
        _positive_integer(self.schema_version, 'schema_version')
        if not isinstance(self.watermark, KpiWatermark):
            raise KpiDeliveryValidationError('watermark must be KpiWatermark')
        object.__setattr__(
            self,
            'configuration_revision',
            _required_text(self.configuration_revision, 'configuration_revision'),
        )
        object.__setattr__(
            self,
            'tool_projection_revision',
            _required_text(self.tool_projection_revision, 'tool_projection_revision'),
        )
        object.__setattr__(
            self,
            'published_at_utc',
            _utc_datetime(self.published_at_utc, 'published_at_utc'),
        )
        object.__setattr__(self, 'revision', _required_text(self.revision, 'revision'))

    def as_payload(self) -> dict[str, object]:
        return {
            'schema_version': self.schema_version,
            'watermark_utc': self.watermark.text,
            'configuration_revision': self.configuration_revision,
            'tool_projection_revision': self.tool_projection_revision,
            'published_at_utc': _format_utc(self.published_at_utc),
            'revision': self.revision,
        }


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesSnapshot:
    id: str
    partition_id: str
    document_type: str
    manifest: KpiTimeseriesManifest
    end_utc: datetime
    step_seconds: int
    destinations: Mapping[str, tuple[str, ...]]
    windows: tuple[KpiTimeseriesWindow, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, 'id', _required_text(self.id, 'id'))
        object.__setattr__(self, 'partition_id', _required_text(self.partition_id, 'partition_id'))
        object.__setattr__(
            self, 'document_type', _required_text(self.document_type, 'document_type')
        )
        if not isinstance(self.manifest, KpiTimeseriesManifest):
            raise KpiDeliveryValidationError('manifest must be KpiTimeseriesManifest')
        end_utc = _utc_datetime(self.end_utc, 'end_utc')
        if end_utc != self.manifest.watermark.timestamp_utc:
            raise KpiDeliveryValidationError('end_utc must match manifest watermark')
        _positive_integer(self.step_seconds, 'step_seconds')
        destinations = _normalize_series_destinations(self.destinations)
        windows = tuple(self.windows)
        if not all(isinstance(window, KpiTimeseriesWindow) for window in windows):
            raise KpiDeliveryValidationError('windows must contain KpiTimeseriesWindow values')
        hours = tuple(window.hours for window in windows)
        if len(hours) != len(set(hours)):
            raise KpiDeliveryValidationError('timeseries window hours must be unique')
        # Cada fila usa la convención temporal (start, end] y debe tener un tamaño determinista.
        for window in windows:
            expected_start = end_utc - timedelta(hours=window.hours)
            if window.start_utc != expected_start:
                raise KpiDeliveryValidationError(
                    'timeseries window start_utc must equal end_utc minus hours'
                )
            duration_seconds = window.hours * 3600
            if duration_seconds % self.step_seconds != 0:
                raise KpiDeliveryValidationError(
                    'timeseries window duration must be divisible by step_seconds'
                )
            expected_points = duration_seconds // self.step_seconds
            if any(len(row) != expected_points for row in window.values):
                raise KpiDeliveryValidationError(
                    'timeseries row length must match hours and step_seconds'
                )
        object.__setattr__(self, 'end_utc', end_utc)
        object.__setattr__(self, 'destinations', destinations)
        object.__setattr__(self, 'windows', windows)


# La función mantiene una operación pequeña y verificable de esta frontera.
def _normalize_destinations(
    destinations: Mapping[str, Mapping[str, KpiDeliveryValue]],
) -> Mapping[str, Mapping[str, KpiDeliveryValue]]:
    if not isinstance(destinations, Mapping):
        raise KpiDeliveryValidationError('destinations must be a mapping')
    normalized: dict[str, Mapping[str, KpiDeliveryValue]] = {}
    for raw_destination, raw_values in destinations.items():
        destination = _required_text(raw_destination, 'destination key')
        if not isinstance(raw_values, Mapping):
            raise KpiDeliveryValidationError(f'{destination}: values must be a mapping')
        values: dict[str, KpiDeliveryValue] = {}
        for raw_key, value in raw_values.items():
            key = _required_text(raw_key, f'{destination}: KPI key')
            if not isinstance(value, KpiDeliveryValue):
                raise KpiDeliveryValidationError(
                    f'{destination}/{key}: value must be KpiDeliveryValue'
                )
            values[key] = value
        normalized[destination] = MappingProxyType(values)
    return MappingProxyType(normalized)


# La función mantiene una operación pequeña y verificable de esta frontera.
def _normalize_series_destinations(
    destinations: Mapping[str, tuple[str, ...]],
) -> Mapping[str, tuple[str, ...]]:
    if not isinstance(destinations, Mapping):
        raise KpiDeliveryValidationError('destinations must be a mapping')
    normalized: dict[str, tuple[str, ...]] = {}
    for raw_destination, raw_keys in destinations.items():
        destination = _required_text(raw_destination, 'destination key')
        keys = tuple(_required_text(key, f'{destination}: KPI key') for key in raw_keys)
        if len(keys) != len(set(keys)):
            raise KpiDeliveryValidationError(f'{destination}: KPI keys must not contain duplicates')
        normalized[destination] = keys
    return MappingProxyType(normalized)


# La función mantiene una operación pequeña y verificable de esta frontera.
def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise KpiDeliveryValidationError(f'{field_name} must be a non-empty string')
    if value != value.strip():
        raise KpiDeliveryValidationError(f'{field_name} must not contain surrounding whitespace')
    return value


# La función mantiene una operación pequeña y verificable de esta frontera.
def _positive_integer(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise KpiDeliveryValidationError(f'{field_name} must be a positive integer')
    return value


# La función mantiene una operación pequeña y verificable de esta frontera.
def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise KpiDeliveryValidationError(f'{field_name} must be datetime')
    if value.tzinfo is None or value.utcoffset() is None:
        raise KpiDeliveryValidationError(f'{field_name} must be timezone-aware')
    return value.astimezone(UTC)


# La función mantiene una operación pequeña y verificable de esta frontera.
def _format_utc(value: datetime) -> str:
    return value.isoformat(timespec='microseconds').replace('+00:00', 'Z')


# La función mantiene una operación pequeña y verificable de esta frontera.
def _validate_series_value(value: object) -> None:
    if value is None or isinstance(value, str | int):
        if isinstance(value, bool):
            raise KpiDeliveryValidationError('timeseries value must not be boolean')
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise KpiDeliveryValidationError('timeseries value must be finite')
        return
    raise KpiDeliveryValidationError('timeseries value must be numeric, text, or null')


# La función mantiene una operación pequeña y verificable de esta frontera.
def _validate_json_value(value: object, field_name: str) -> None:
    _reject_non_finite_numbers(value, field_name)
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise KpiDeliveryValidationError(f'{field_name} must be JSON serializable') from error


# La función mantiene una operación pequeña y verificable de esta frontera.
def _reject_non_finite_numbers(value: object, field_name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise KpiDeliveryValidationError(f'{field_name} must not contain non-finite numbers')
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise KpiDeliveryValidationError(f'{field_name} mapping keys must be strings')
            _reject_non_finite_numbers(nested, field_name)
    elif isinstance(value, list | tuple):
        for nested in value:
            _reject_non_finite_numbers(nested, field_name)
