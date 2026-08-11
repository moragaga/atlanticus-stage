# La clave define la ruta; el documento conserva únicamente el último valor confirmado.
"""Identidad y sobre persistido del estado."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from atlanticus.state.errors import (
    StateCorruptionError,
    StateSchemaError,
    StateValidationError,
)
from atlanticus.state.serialization import (
    FrozenJsonObject,
    JsonObject,
    freeze_json_object,
    normalize_json_object,
)

STATE_SCHEMA_VERSION = 1
_IDENTITY_PATTERN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,119})?')


@dataclass(frozen=True, slots=True)
class StateKey:
    """Ruta lógica extensible que no conoce fuentes ni dominios de negocio."""

    namespace: tuple[str, ...]
    name: str

    def __post_init__(self) -> None:
        # No se normalizan listas a tuplas: el contrato debe ser correcto desde su construcción.
        if not isinstance(self.namespace, tuple):
            raise StateValidationError('state namespace must be a tuple of path segments')
        if not self.namespace:
            raise StateValidationError('state namespace must not be empty')
        for segment in (*self.namespace, self.name):
            _validate_identity_segment(segment)
        if self.name.endswith('.json'):
            raise StateValidationError('state name must not include the .json extension')

    @property
    def identifier(self) -> str:
        """Representación estable apta para diagnósticos sin exponer el volumen."""

        return '/'.join((*self.namespace, self.name))

    @property
    def relative_path(self) -> Path:
        """Ruta relativa segura dentro del scope de la aplicación."""

        return Path(*self.namespace, f'{self.name}.json')


# La key no se duplica dentro del JSON porque su ruta ya es la identidad del documento.
@dataclass(frozen=True, slots=True)
class StateDocument:
    """Último valor confirmado para una clave lógica."""

    key: StateKey
    updated_at_utc: datetime
    value: FrozenJsonObject
    schema_version: int = STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.key, StateKey):
            raise StateValidationError('key must be a StateKey')
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version != STATE_SCHEMA_VERSION
        ):
            raise StateSchemaError(f'unsupported state schema version: {self.schema_version}')
        if not isinstance(self.updated_at_utc, datetime):
            raise StateValidationError('updated_at_utc must be a datetime')
        if self.updated_at_utc.tzinfo is None:
            raise StateValidationError('updated_at_utc must be timezone-aware')
        object.__setattr__(self, 'updated_at_utc', self.updated_at_utc.astimezone(UTC))
        # El snapshot no comparte listas ni diccionarios mutables con el consumidor.
        object.__setattr__(self, 'value', freeze_json_object(self.value))

    def to_payload(self) -> JsonObject:
        """Construye el documento mínimo; la clave ya está expresada por la ruta."""

        return {
            'schema_version': self.schema_version,
            'updated_at_utc': _format_utc(self.updated_at_utc),
            'value': normalize_json_object(self.value),
        }

    @classmethod
    def from_payload(cls, key: StateKey, payload: Mapping[str, Any]) -> Self:
        """Valida de forma estricta un documento leído desde storage."""

        if not isinstance(payload, Mapping):
            raise StateCorruptionError('state document must be a JSON object')
        schema_version = payload.get('schema_version')
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise StateCorruptionError('state schema_version must be an integer')
        if schema_version != STATE_SCHEMA_VERSION:
            raise StateSchemaError(f'unsupported state schema version: {schema_version}')
        # Campos adicionales exigen una nueva versión de schema y una migración deliberada.
        expected_fields = {'schema_version', 'updated_at_utc', 'value'}
        if set(payload) != expected_fields:
            raise StateCorruptionError('state document contains unexpected or missing fields')
        updated_at_raw = payload['updated_at_utc']
        if not isinstance(updated_at_raw, str):
            raise StateCorruptionError('state updated_at_utc must be a string')
        try:
            updated_at = datetime.fromisoformat(updated_at_raw.replace('Z', '+00:00'))
        except ValueError as error:
            raise StateCorruptionError('state updated_at_utc is invalid') from error
        if updated_at.tzinfo is None:
            raise StateCorruptionError('state updated_at_utc must be timezone-aware')
        value = payload['value']
        if not isinstance(value, Mapping):
            raise StateCorruptionError('state value must be a JSON object')
        try:
            normalized_value = normalize_json_object(value)
        except StateValidationError as error:
            raise StateCorruptionError('state value is invalid') from error
        return cls(
            key=key,
            updated_at_utc=updated_at,
            value=normalized_value,
            schema_version=schema_version,
        )


def validate_application(application: str) -> str:
    """Valida la identidad sin normalizarla silenciosamente ni crear colisiones."""

    _validate_identity_segment(application)
    return application


# Rechazar es preferible a reemplazar caracteres: dos aplicaciones nunca colisionan silenciosamente.
def _validate_identity_segment(value: str) -> None:
    if not isinstance(value, str) or not _IDENTITY_PATTERN.fullmatch(value):
        raise StateValidationError(
            'state path segments must use 1-120 letters, numbers, dots, underscores or hyphens'
        )
    if value in {'.', '..'}:
        raise StateValidationError('state path segments must not be relative paths')


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec='microseconds').replace('+00:00', 'Z')
