# Contratos inmutables que representan la selección de consumo y el snapshot que verá el frontend.
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ada.kpis.core import KpiValueKind
from ada.kpis.delivery.errors import KpiDeliveryValidationError


class KpiDeliveryStatus(StrEnum):
    OK = 'ok'
    ERROR = 'error'
    MISSING = 'missing'


@dataclass(frozen=True, slots=True)
class KpiDeliveryBinding:
    store_key: str
    kpi_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, 'store_key', _required_text(self.store_key, 'store_key'))
        object.__setattr__(self, 'kpi_key', _required_text(self.kpi_key, 'kpi_key'))


@dataclass(frozen=True, slots=True)
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
                raise KpiDeliveryValidationError('missing value must not contain value_kind or value')
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
class KpiDeliveryManifest:
    schema_version: int
    updated_at_utc: datetime
    revision: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version <= 0
        ):
            raise KpiDeliveryValidationError('schema_version must be a positive integer')
        object.__setattr__(self, 'updated_at_utc', _utc_datetime(self.updated_at_utc))
        object.__setattr__(self, 'revision', _required_text(self.revision, 'revision'))

    def as_payload(self) -> dict[str, object]:
        return {
            'schema_version': self.schema_version,
            'updated_at_utc': _format_utc(self.updated_at_utc),
            'revision': self.revision,
        }


@dataclass(frozen=True, slots=True)
class KpiDeliverySnapshot:
    id: str
    partition_id: str
    manifest: KpiDeliveryManifest
    stores: Mapping[str, Mapping[str, KpiDeliveryValue]]

    def __post_init__(self) -> None:
        object.__setattr__(self, 'id', _required_text(self.id, 'id'))
        object.__setattr__(self, 'partition_id', _required_text(self.partition_id, 'partition_id'))
        if not isinstance(self.manifest, KpiDeliveryManifest):
            raise KpiDeliveryValidationError('manifest must be KpiDeliveryManifest')
        object.__setattr__(self, 'stores', _normalize_stores(self.stores))

    def as_document(self) -> dict[str, object]:
        return {
            'id': self.id,
            'partition_id': self.partition_id,
            'manifest': self.manifest.as_payload(),
            'stores': {
                store_key: {
                    kpi_key: value.as_payload() for kpi_key, value in values.items()
                }
                for store_key, values in self.stores.items()
            },
        }


def _normalize_stores(
    stores: Mapping[str, Mapping[str, KpiDeliveryValue]],
) -> Mapping[str, Mapping[str, KpiDeliveryValue]]:
    if not isinstance(stores, Mapping):
        raise KpiDeliveryValidationError('stores must be a mapping')
    normalized: dict[str, Mapping[str, KpiDeliveryValue]] = {}
    for raw_store_key, raw_values in stores.items():
        store_key = _required_text(raw_store_key, 'store key')
        if not isinstance(raw_values, Mapping):
            raise KpiDeliveryValidationError(f'{store_key}: values must be a mapping')
        values: dict[str, KpiDeliveryValue] = {}
        for raw_kpi_key, value in raw_values.items():
            kpi_key = _required_text(raw_kpi_key, f'{store_key}: kpi key')
            if not isinstance(value, KpiDeliveryValue):
                raise KpiDeliveryValidationError(
                    f'{store_key}/{kpi_key}: value must be KpiDeliveryValue'
                )
            values[kpi_key] = value
        normalized[store_key] = MappingProxyType(values)
    return MappingProxyType(normalized)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise KpiDeliveryValidationError(f'{field_name} must be a non-empty string')
    if value != value.strip():
        raise KpiDeliveryValidationError(f'{field_name} must not contain surrounding whitespace')
    return value


def _utc_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise KpiDeliveryValidationError('updated_at_utc must be datetime')
    if value.tzinfo is None or value.utcoffset() is None:
        raise KpiDeliveryValidationError('updated_at_utc must be timezone-aware')
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _validate_json_value(value: object, field_name: str) -> None:
    _reject_non_finite_numbers(value, field_name)
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise KpiDeliveryValidationError(f'{field_name} must be JSON serializable') from error


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
