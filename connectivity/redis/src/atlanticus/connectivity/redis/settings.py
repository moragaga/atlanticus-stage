"""Configuración tipada y explícita para una conexión Redis."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from atlanticus.connectivity.redis.errors import RedisConfigurationError

DEFAULT_REDIS_PORT = 6379
DEFAULT_REDIS_DATABASE = 0
DEFAULT_REDIS_CONNECTION_TIMEOUT_SECONDS = 5
DEFAULT_REDIS_OPERATION_TIMEOUT_SECONDS = 5
DEFAULT_REDIS_MAX_CONNECTIONS = 32
DEFAULT_REDIS_MAX_MGET_KEYS = 1_000


@dataclass(frozen=True, slots=True)
class _RedisEndpoint:
    """Endpoint Redis ya validado y separado de las credenciales."""

    scheme: str
    host: str
    port: int

    @property
    def tls_enabled(self) -> bool:
        return self.scheme == 'rediss'

    @property
    def url(self) -> str:
        host = f'[{self.host}]' if ':' in self.host and not self.host.startswith('[') else self.host
        return f'{self.scheme}://{host}:{self.port}'


@dataclass(frozen=True, slots=True)
class RedisSettings:
    """Endpoint, autenticación y límites operacionales neutrales para Redis."""

    url: str
    username: str
    password: str = field(repr=False)
    database: int = DEFAULT_REDIS_DATABASE
    allow_insecure_transport: bool = False
    connection_timeout_seconds: int = DEFAULT_REDIS_CONNECTION_TIMEOUT_SECONDS
    operation_timeout_seconds: int = DEFAULT_REDIS_OPERATION_TIMEOUT_SECONDS
    max_connections: int = DEFAULT_REDIS_MAX_CONNECTIONS
    max_mget_keys: int = DEFAULT_REDIS_MAX_MGET_KEYS

    def __post_init__(self) -> None:
        endpoint = _parse_redis_endpoint(self.url)
        object.__setattr__(self, 'url', endpoint.url)
        object.__setattr__(self, 'username', _require_identity(self.username, 'username'))
        object.__setattr__(self, 'password', _require_secret(self.password, 'password'))
        object.__setattr__(
            self, 'database', _require_non_negative_integer(self.database, 'database')
        )
        allow_insecure_transport = _require_bool(
            self.allow_insecure_transport,
            'allow_insecure_transport',
        )
        if not endpoint.tls_enabled and not allow_insecure_transport:
            raise RedisConfigurationError('redis:// requires allow_insecure_transport=True')
        object.__setattr__(self, 'allow_insecure_transport', allow_insecure_transport)
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
            'operation_timeout_seconds',
            _require_positive_integer(
                self.operation_timeout_seconds,
                'operation_timeout_seconds',
            ),
        )
        object.__setattr__(
            self,
            'max_connections',
            _require_positive_integer(self.max_connections, 'max_connections'),
        )
        object.__setattr__(
            self,
            'max_mget_keys',
            _require_positive_integer(self.max_mget_keys, 'max_mget_keys'),
        )


def _parse_redis_endpoint(value: Any) -> _RedisEndpoint:
    """Valida una URL Redis sin credenciales, database, query ni fragment."""

    if not isinstance(value, str):
        raise RedisConfigurationError('url must be text')
    normalized = value.strip()
    if not normalized:
        raise RedisConfigurationError('url is required')
    if any(character in normalized for character in '\x00\r\n'):
        raise RedisConfigurationError('url must not contain control characters')
    try:
        parsed = urlsplit(normalized)
        port = DEFAULT_REDIS_PORT if parsed.port is None else parsed.port
    except ValueError:
        raise RedisConfigurationError('url contains an invalid port') from None
    scheme = parsed.scheme.casefold()
    if scheme not in {'redis', 'rediss'}:
        raise RedisConfigurationError('url scheme must be redis:// or rediss://')
    if parsed.username is not None or parsed.password is not None:
        raise RedisConfigurationError('url must not contain Redis credentials')
    if not parsed.hostname:
        raise RedisConfigurationError('url must contain a Redis host')
    if parsed.path not in {'', '/'}:
        raise RedisConfigurationError('url must not contain a database or path')
    if parsed.query:
        raise RedisConfigurationError('url must not contain query parameters')
    if parsed.fragment:
        raise RedisConfigurationError('url must not contain a fragment')
    if not 1 <= port <= 65535:
        raise RedisConfigurationError('url port must be between 1 and 65535')
    return _RedisEndpoint(scheme=scheme, host=parsed.hostname, port=port)


def _require_identity(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise RedisConfigurationError(f'{field_name} must be text')
    if value == '':
        raise RedisConfigurationError(f'{field_name} is required')
    if any(character in value for character in '\x00\r\n'):
        raise RedisConfigurationError(f'{field_name} must not contain control characters')
    return value


def _require_secret(value: Any, field_name: str) -> str:
    return _require_identity(value, field_name)


def _require_positive_integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RedisConfigurationError(f'{field_name} must be a positive integer')
    return value


def _require_non_negative_integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RedisConfigurationError(f'{field_name} must be a non-negative integer')
    return value


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise RedisConfigurationError(f'{field_name} must be a boolean')
    return value
