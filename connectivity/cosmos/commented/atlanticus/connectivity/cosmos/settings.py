# Espejo pedagógico: conserva exactamente el código productivo y agrega sólo comentarios.
# La composición entrega settings ya resueltos; este módulo no interpreta variables de entorno.
"""Configuración inmutable y explícita para una conexión Cosmos DB."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from atlanticus.connectivity.cosmos.errors import CosmosConfigurationError

DEFAULT_COSMOS_CONNECTION_TIMEOUT_SECONDS = 10
DEFAULT_COSMOS_REQUEST_TIMEOUT_SECONDS = 65
DEFAULT_COSMOS_MAX_QUERY_ITEMS = 10_000
DEFAULT_COSMOS_PAGE_SIZE = 250


@dataclass(frozen=True, slots=True)
# Contrato/clase CosmosSettings: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosSettings:
    """Endpoint, credencial, base y límites ya resueltos por composición."""

    endpoint: str
    key: str = field(repr=False)
    database_name: str
    connection_timeout_seconds: int = DEFAULT_COSMOS_CONNECTION_TIMEOUT_SECONDS
    request_timeout_seconds: int = DEFAULT_COSMOS_REQUEST_TIMEOUT_SECONDS
    max_query_items: int = DEFAULT_COSMOS_MAX_QUERY_ITEMS
    page_size: int = DEFAULT_COSMOS_PAGE_SIZE
    allow_insecure_http: bool = False

    # Helper interno __post_init__: valida o adapta datos antes de tocar el SDK.
    def __post_init__(self) -> None:
        allow_insecure_http = _require_bool(self.allow_insecure_http, 'allow_insecure_http')
        object.__setattr__(self, 'allow_insecure_http', allow_insecure_http)
        object.__setattr__(
            self,
            'endpoint',
            _normalize_endpoint(
                self.endpoint,
                allow_insecure_http=allow_insecure_http,
            ),
        )
        object.__setattr__(self, 'key', _require_secret(self.key, 'key'))
        object.__setattr__(
            self,
            'database_name',
            _require_identifier(self.database_name, 'database_name', max_length=255),
        )
        object.__setattr__(
            self,
            'connection_timeout_seconds',
            _require_positive_integer(
                self.connection_timeout_seconds,
                'connection_timeout_seconds',
            ),
        )
        object.__setattr__(
            self,
            'request_timeout_seconds',
            _require_positive_integer(
                self.request_timeout_seconds,
                'request_timeout_seconds',
            ),
        )
        object.__setattr__(
            self,
            'max_query_items',
            _require_positive_integer(self.max_query_items, 'max_query_items'),
        )
        object.__setattr__(
            self,
            'page_size',
            _require_positive_integer(self.page_size, 'page_size'),
        )


# Operación sanitize_endpoint: expone una frontera explícita y sanitizada del conector.
def sanitize_endpoint(endpoint: str) -> str:
    """Retorna únicamente esquema, host y puerto para diagnósticos seguros."""

    if not isinstance(endpoint, str):
        return '<invalid>'
    try:
        parsed = urlsplit(endpoint)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return '<invalid>'
    if not parsed.scheme or hostname is None:
        return '<invalid>'
    authority = f'[{hostname}]' if ':' in hostname else hostname
    if port is not None:
        authority = f'{authority}:{port}'
    return urlunsplit((parsed.scheme.lower(), authority, '', '', ''))


# Helper interno _normalize_endpoint: valida o adapta datos antes de tocar el SDK.
def _normalize_endpoint(value: Any, *, allow_insecure_http: bool) -> str:
    if not isinstance(value, str):
        raise CosmosConfigurationError('endpoint must be text')
    normalized = value.strip()
    if not normalized:
        raise CosmosConfigurationError('endpoint is required')
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError:
        raise CosmosConfigurationError('endpoint must be a valid HTTP or HTTPS URL') from None
    scheme = parsed.scheme.lower()
    if scheme not in {'https', 'http'} or not parsed.netloc or parsed.hostname is None:
        raise CosmosConfigurationError('endpoint must be an absolute HTTP or HTTPS URL')
    if port is not None and not 1 <= port <= 65535:
        raise CosmosConfigurationError('endpoint must contain a valid port')
    if parsed.username is not None or parsed.password is not None:
        raise CosmosConfigurationError('endpoint must not contain credentials')
    if parsed.query or parsed.fragment:
        raise CosmosConfigurationError('endpoint must not contain query parameters or fragments')
    if parsed.path not in {'', '/'}:
        raise CosmosConfigurationError('endpoint must not contain a path')
    if scheme == 'http' and not allow_insecure_http:
        raise CosmosConfigurationError('HTTP endpoint requires allow_insecure_http=True')
    return urlunsplit((scheme, parsed.netloc, '', '', ''))


# Helper interno _require_secret: valida o adapta datos antes de tocar el SDK.
def _require_secret(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise CosmosConfigurationError(f'{field_name} must be text')
    if value == '':
        raise CosmosConfigurationError(f'{field_name} is required')
    if '\x00' in value:
        raise CosmosConfigurationError(f'{field_name} must not contain null characters')
    return value


# Helper interno _require_identifier: valida o adapta datos antes de tocar el SDK.
def _require_identifier(value: Any, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise CosmosConfigurationError(f'{field_name} must be text')
    if not value or not value.strip():
        raise CosmosConfigurationError(f'{field_name} is required')
    if value != value.strip():
        raise CosmosConfigurationError(f'{field_name} must not contain surrounding whitespace')
    if '\x00' in value:
        raise CosmosConfigurationError(f'{field_name} must not contain null characters')
    if any(character in value for character in '/\\#?\t\r\n'):
        raise CosmosConfigurationError(f'{field_name} contains unsupported Cosmos characters')
    if len(value) > max_length:
        raise CosmosConfigurationError(f'{field_name} must contain at most {max_length} characters')
    return value


# Helper interno _require_positive_integer: valida o adapta datos antes de tocar el SDK.
def _require_positive_integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CosmosConfigurationError(f'{field_name} must be a positive integer')
    return value


# Helper interno _require_bool: valida o adapta datos antes de tocar el SDK.
def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise CosmosConfigurationError(f'{field_name} must be a boolean')
    return value
