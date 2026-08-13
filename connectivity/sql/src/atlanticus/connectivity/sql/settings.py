"""Configuración inmutable para una conexión SQL completa e inyectada."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from atlanticus.connectivity.sql.errors import SqlConfigurationError

DEFAULT_SQL_QUERY_TIMEOUT_SECONDS = 60
DEFAULT_SQL_BATCH_SIZE = 10_000
DEFAULT_SQL_MAX_QUERY_ROWS = 10_000

_SUFFIX_PATTERN = re.compile(r'^[A-Z0-9]+(?:_[A-Z0-9]+)*$')
_SUPPORTED_LEGACY_DRIVERS = frozenset(
    {
        'odbc driver 17 for sql server',
        'odbc driver 18 for sql server',
    }
)


@dataclass(frozen=True, slots=True)
class SqlConfigurationKeys:
    """Claves exactas resueltas para una conexión SQL concreta."""

    connection_string: str
    query_timeout_seconds: str
    batch_size: str
    max_query_rows: str


@dataclass(frozen=True, slots=True)
class SqlSettings:
    """Connection string y límites de lectura sin consultar el entorno."""

    connection_string: str = field(repr=False)
    query_timeout_seconds: int = DEFAULT_SQL_QUERY_TIMEOUT_SECONDS
    batch_size: int = DEFAULT_SQL_BATCH_SIZE
    max_query_rows: int = DEFAULT_SQL_MAX_QUERY_ROWS
    suffix: str | None = None

    def __post_init__(self) -> None:
        connection_string = normalize_connection_string(self.connection_string)
        _prepare_mssql_connection(connection_string)
        object.__setattr__(self, 'connection_string', connection_string)
        object.__setattr__(
            self,
            'query_timeout_seconds',
            require_positive_integer(self.query_timeout_seconds, 'query_timeout_seconds'),
        )
        object.__setattr__(
            self,
            'batch_size',
            require_positive_integer(self.batch_size, 'batch_size'),
        )
        object.__setattr__(
            self,
            'max_query_rows',
            require_positive_integer(self.max_query_rows, 'max_query_rows'),
        )
        object.__setattr__(self, 'suffix', normalize_configuration_suffix(self.suffix))

    @classmethod
    def from_mapping(
        cls,
        *,
        values: Mapping[str, Any],
        suffix: str | None = None,
    ) -> SqlSettings:
        """Construye settings desde valores resueltos por otra capa."""

        normalized_suffix = normalize_configuration_suffix(suffix)
        keys = build_sql_configuration_keys(suffix=normalized_suffix)
        if optional_mapping_text(values, keys.connection_string) is None:
            raise SqlConfigurationError(f'Missing SQL configuration key: {keys.connection_string}')
        return cls(
            connection_string=require_mapping_text(values, keys.connection_string),
            query_timeout_seconds=parse_mapping_integer(
                values,
                keys.query_timeout_seconds,
                default=DEFAULT_SQL_QUERY_TIMEOUT_SECONDS,
            ),
            batch_size=parse_mapping_integer(
                values,
                keys.batch_size,
                default=DEFAULT_SQL_BATCH_SIZE,
            ),
            max_query_rows=parse_mapping_integer(
                values,
                keys.max_query_rows,
                default=DEFAULT_SQL_MAX_QUERY_ROWS,
            ),
            suffix=normalized_suffix,
        )


def build_sql_configuration_keys(*, suffix: str | None = None) -> SqlConfigurationKeys:
    """Construye claves base o sufijadas para conexiones independientes."""

    normalized_suffix = normalize_configuration_suffix(suffix)

    def key(name: str) -> str:
        base = f'SQL_{name}'
        return base if normalized_suffix is None else f'{base}_{normalized_suffix}'

    return SqlConfigurationKeys(
        connection_string=key('CONNECTION_STRING'),
        query_timeout_seconds=key('QUERY_TIMEOUT_SECONDS'),
        batch_size=key('BATCH_SIZE'),
        max_query_rows=key('MAX_QUERY_ROWS'),
    )


def normalize_configuration_suffix(value: str | None) -> str | None:
    """Normaliza el sufijo lógico usado por una conexión concreta."""

    if value is None:
        return None
    normalized = str(value).strip().strip('_').upper()
    if not normalized:
        return None
    if _SUFFIX_PATTERN.fullmatch(normalized) is None:
        raise SqlConfigurationError(
            'SQL configuration suffix must contain only letters, numbers and single underscores'
        )
    return normalized


def normalize_connection_string(value: Any) -> str:
    """Valida la connection string sin exponer ni reescribir sus secretos."""

    if not isinstance(value, str):
        raise SqlConfigurationError('connection_string must be a string')
    if not value.strip():
        raise SqlConfigurationError('connection_string is required')
    if '=' not in value:
        raise SqlConfigurationError('connection_string must contain SQL key-value properties')
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise SqlConfigurationError('connection_string must not contain control characters')
    return value


def _prepare_mssql_connection(value: str) -> tuple[str, int | None]:
    segments = _split_connection_string(value)
    found_driver = False
    found_connection_timeout = False
    connection_timeout_seconds: int | None = None
    runtime_segments: list[str] = []
    for segment in segments:
        parts = segment.split('=', 1)
        if len(parts) != 2:
            runtime_segments.append(segment)
            continue
        key = parts[0].strip().casefold()
        if key == 'driver':
            if found_driver:
                raise SqlConfigurationError(
                    'connection_string must not contain duplicate Driver properties'
                )
            found_driver = True
            driver_name = _normalize_legacy_driver_name(parts[1])
            if driver_name not in _SUPPORTED_LEGACY_DRIVERS:
                raise SqlConfigurationError(
                    'connection_string Driver must be ODBC Driver 17 or 18 for SQL Server'
                )
            continue
        if key == 'connection timeout':
            if found_connection_timeout:
                raise SqlConfigurationError(
                    'connection_string must not contain duplicate Connection Timeout properties'
                )
            found_connection_timeout = True
            connection_timeout_seconds = _parse_connection_timeout(parts[1])
            continue
        runtime_segments.append(segment)
    runtime = ';'.join(runtime_segments)
    if not runtime.strip(' ;') or '=' not in runtime:
        raise SqlConfigurationError(
            'connection_string must contain SQL properties besides compatibility properties'
        )
    return runtime, connection_timeout_seconds


def _parse_connection_timeout(value: str) -> int:
    normalized = value.strip()
    if not normalized.isdecimal():
        raise SqlConfigurationError(
            'connection_string Connection Timeout must be a non-negative integer'
        )
    return int(normalized)


def _split_connection_string(value: str) -> tuple[str, ...]:
    segments: list[str] = []
    segment_start = 0
    in_braces = False
    index = 0
    while index < len(value):
        character = value[index]
        if character == '{' and not in_braces:
            in_braces = True
        elif character == '}' and in_braces:
            if index + 1 < len(value) and value[index + 1] == '}':
                index += 2
                continue
            in_braces = False
        elif character == ';' and not in_braces:
            segments.append(value[segment_start:index])
            segment_start = index + 1
        index += 1
    if in_braces:
        raise SqlConfigurationError('connection_string contains an unterminated braced value')
    segments.append(value[segment_start:])
    return tuple(segments)


def _normalize_legacy_driver_name(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith('{') and normalized.endswith('}'):
        normalized = normalized[1:-1]
    return ' '.join(normalized.split()).casefold()


def parse_mapping_integer(
    values: Mapping[str, Any],
    key: str,
    *,
    default: int,
) -> int:
    """Obtiene un entero positivo opcional con un error asociado a su clave."""

    value = values.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    return require_positive_integer(value, key)


def require_positive_integer(value: Any, field_name: str) -> int:
    """Exige un entero real mayor que cero, sin redondear decimales."""

    if isinstance(value, bool):
        raise SqlConfigurationError(f'{field_name} must be an integer greater than zero')
    try:
        parsed = int(value)
    except TypeError, ValueError:
        raise SqlConfigurationError(f'{field_name} must be an integer greater than zero') from None
    if parsed <= 0 or str(value).strip() not in {str(parsed), f'+{parsed}'}:
        raise SqlConfigurationError(f'{field_name} must be an integer greater than zero')
    return parsed


def optional_mapping_text(values: Mapping[str, Any], key: str) -> str | None:
    """Obtiene texto opcional sin asumir que el mapping proviene del entorno."""

    value = values.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    normalized = str(value)
    return normalized if normalized.strip() else None


def require_mapping_text(values: Mapping[str, Any], key: str) -> str:
    """Obtiene una clave obligatoria sin exponer su valor en el error."""

    value = optional_mapping_text(values, key)
    if value is None:
        raise SqlConfigurationError(f'Missing SQL configuration key: {key}')
    return value
