"""Modelos neutrales sin proxies ni respuestas internas del SDK."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from atlanticus.connectivity.cosmos.errors import CosmosConfigurationError

T = TypeVar('T')

_ALLOWED_PATCH_OPERATIONS = frozenset({'add', 'remove', 'replace', 'set', 'incr', 'move'})
_VALUE_OPERATIONS = frozenset({'add', 'replace', 'set', 'incr'})


@dataclass(frozen=True, slots=True)
class CosmosQueryParameter:
    """Parámetro nombrado enviado de forma segura al motor SQL de Cosmos."""

    name: str
    value: Any

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise CosmosConfigurationError('Cosmos query parameter name must be text')
        if self.name != self.name.strip():
            raise CosmosConfigurationError(
                'Cosmos query parameter name must not contain surrounding whitespace'
            )
        if not self.name.startswith('@') or len(self.name) == 1:
            raise CosmosConfigurationError('Cosmos query parameter name must start with @')
        if '\x00' in self.name:
            raise CosmosConfigurationError(
                'Cosmos query parameter name must not contain null characters'
            )

    def as_sdk_value(self) -> dict[str, Any]:
        """Construye el diccionario neutral requerido por el SDK."""

        return {'name': self.name, 'value': self.value}


@dataclass(frozen=True, slots=True)
class CosmosPatchOperation:
    """Operación patch validada antes de invocar Azure Cosmos."""

    operation: str
    path: str
    value: Any = None
    from_path: str | None = None

    def __post_init__(self) -> None:
        operation = self.operation.strip().lower() if isinstance(self.operation, str) else ''
        path = _require_json_path(self.path, 'path')
        if operation not in _ALLOWED_PATCH_OPERATIONS:
            raise CosmosConfigurationError(f'Unsupported Cosmos patch operation: {operation!r}')
        if operation == 'move':
            if self.from_path is None:
                raise CosmosConfigurationError('Cosmos move patch requires from_path')
            object.__setattr__(self, 'from_path', _require_json_path(self.from_path, 'from_path'))
        elif self.from_path is not None:
            raise CosmosConfigurationError('from_path is only valid for Cosmos move patch')
        object.__setattr__(self, 'operation', operation)
        object.__setattr__(self, 'path', path)

    def as_sdk_value(self) -> dict[str, Any]:
        """Convierte la operación al contrato estable de ``patch_item``."""

        result: dict[str, Any] = {'op': self.operation, 'path': self.path}
        if self.operation in _VALUE_OPERATIONS:
            result['value'] = self.value
        if self.operation == 'move':
            result['from'] = self.from_path
        return result


@dataclass(frozen=True, slots=True)
class CosmosPage(Generic[T]):
    """Una página materializada y su token opaco para la siguiente lectura."""

    items: tuple[T, ...]
    continuation_token: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'items', tuple(self.items))
        token = self.continuation_token
        if token is not None and (not isinstance(token, str) or not token):
            raise CosmosConfigurationError('continuation_token must be a non-empty string or None')

    @property
    def item_count(self) -> int:
        """Cantidad exacta de elementos de esta página."""

        return len(self.items)


@dataclass(frozen=True, slots=True)
class CosmosContainerSpec:
    """Definición mínima que el bootstrap puede crear y validar."""

    name: str
    partition_key_path: str
    default_ttl_seconds: int | None = None

    def __post_init__(self) -> None:
        name = _require_identifier(self.name, 'Cosmos container name')
        partition_key_path = _require_json_path(
            self.partition_key_path,
            'partition_key_path',
        )
        ttl = self.default_ttl_seconds
        if ttl is not None and (
            not isinstance(ttl, int) or isinstance(ttl, bool) or ttl == 0 or ttl < -1
        ):
            raise CosmosConfigurationError(
                'default_ttl_seconds must be None, -1, or a positive integer'
            )
        object.__setattr__(self, 'name', name)
        object.__setattr__(self, 'partition_key_path', partition_key_path)


def normalize_patch_operations(
    operations: Sequence[CosmosPatchOperation],
) -> tuple[dict[str, Any], ...]:
    """Valida una secuencia no vacía y la convierte al formato del SDK."""

    if isinstance(operations, str | bytes | bytearray | Mapping):
        raise CosmosConfigurationError('patch operations must be a sequence')
    try:
        normalized = tuple(operations)
    except TypeError:
        raise CosmosConfigurationError('patch operations must be a sequence') from None
    if not normalized:
        raise CosmosConfigurationError('patch operations must not be empty')
    if any(not isinstance(operation, CosmosPatchOperation) for operation in normalized):
        raise CosmosConfigurationError('patch operations must contain CosmosPatchOperation values')
    return tuple(operation.as_sdk_value() for operation in normalized)


def _require_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or not value.strip():
        raise CosmosConfigurationError(f'{field_name} must not be empty')
    if value != value.strip():
        raise CosmosConfigurationError(f'{field_name} must not contain surrounding whitespace')
    if '\x00' in value:
        raise CosmosConfigurationError(f'{field_name} must not contain null characters')
    if any(character in value for character in '/\\#?\t\r\n'):
        raise CosmosConfigurationError(f'{field_name} contains unsupported Cosmos characters')
    if len(value) > 255:
        raise CosmosConfigurationError(f'{field_name} must contain at most 255 characters')
    return value


def _require_json_path(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise CosmosConfigurationError(f'{field_name} must be text')
    if value != value.strip():
        raise CosmosConfigurationError(f'{field_name} must not contain surrounding whitespace')
    if not value.startswith('/') or value == '/' or '//' in value:
        raise CosmosConfigurationError(f'{field_name} must be an absolute Cosmos JSON path')
    if '\x00' in value:
        raise CosmosConfigurationError(f'{field_name} must not contain null characters')
    return value
