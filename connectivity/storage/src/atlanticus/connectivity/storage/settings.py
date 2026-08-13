"""Configuración tipada y explícita para una conexión Azure Blob Storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeAlias
from urllib.parse import urlsplit, urlunsplit

from atlanticus.connectivity.storage.errors import StorageConfigurationError

DEFAULT_STORAGE_CONNECTION_TIMEOUT_SECONDS = 20
DEFAULT_STORAGE_READ_TIMEOUT_SECONDS = 60
DEFAULT_STORAGE_MAX_LIST_ITEMS = 10_000


@dataclass(frozen=True, slots=True)
class StorageConnectionStringCredential:
    """Connection string final ya resuelta por la capa de composición."""

    connection_string: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            'connection_string',
            _require_secret(self.connection_string, 'connection_string'),
        )


@dataclass(frozen=True, slots=True)
class StorageSasCredential:
    """URL de cuenta y SAS final ya resueltos por la capa de composición."""

    account_url: str
    sas_token: str = field(repr=False)
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        allow_insecure_http = _require_bool(self.allow_insecure_http, 'allow_insecure_http')
        object.__setattr__(self, 'allow_insecure_http', allow_insecure_http)
        object.__setattr__(
            self,
            'account_url',
            _normalize_account_url(self.account_url, allow_insecure_http=allow_insecure_http),
        )
        object.__setattr__(self, 'sas_token', _require_secret(self.sas_token, 'sas_token'))


StorageCredential: TypeAlias = StorageConnectionStringCredential | StorageSasCredential


@dataclass(frozen=True, slots=True)
class StorageSettings:
    """Credencial y límites operacionales neutrales para un cliente Storage."""

    credential: StorageCredential
    connection_timeout_seconds: int = DEFAULT_STORAGE_CONNECTION_TIMEOUT_SECONDS
    read_timeout_seconds: int = DEFAULT_STORAGE_READ_TIMEOUT_SECONDS
    max_list_items: int = DEFAULT_STORAGE_MAX_LIST_ITEMS

    def __post_init__(self) -> None:
        if not isinstance(
            self.credential,
            StorageConnectionStringCredential | StorageSasCredential,
        ):
            raise StorageConfigurationError(
                'credential must be StorageConnectionStringCredential or StorageSasCredential'
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
            'read_timeout_seconds',
            _require_positive_integer(self.read_timeout_seconds, 'read_timeout_seconds'),
        )
        object.__setattr__(
            self,
            'max_list_items',
            _require_positive_integer(self.max_list_items, 'max_list_items'),
        )


def sanitize_account_url(value: str) -> str:
    """Retorna una URL sin query ni fragment para diagnósticos seguros."""

    if not isinstance(value, str):
        return '<invalid>'
    try:
        parsed = urlsplit(value)
    except ValueError:
        return '<invalid>'
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return '<invalid>'
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip('/'), '', ''))


def _normalize_account_url(value: Any, *, allow_insecure_http: bool) -> str:
    if not isinstance(value, str):
        raise StorageConfigurationError('account_url must be text')
    normalized = value.strip()
    if not normalized:
        raise StorageConfigurationError('account_url is required')
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError:
        raise StorageConfigurationError('account_url must be a valid HTTP or HTTPS URL') from None
    scheme = parsed.scheme.lower()
    if scheme not in {'http', 'https'} or not parsed.netloc or parsed.hostname is None:
        raise StorageConfigurationError('account_url must be an absolute HTTP or HTTPS URL')
    if port is not None and not 1 <= port <= 65535:
        raise StorageConfigurationError('account_url must contain a valid port')
    if parsed.username is not None or parsed.password is not None:
        raise StorageConfigurationError('account_url must not contain credentials')
    if parsed.query or parsed.fragment:
        raise StorageConfigurationError(
            'account_url must not contain query parameters or fragments'
        )
    if scheme == 'http' and not allow_insecure_http:
        raise StorageConfigurationError('HTTP account_url requires allow_insecure_http=True')
    path = parsed.path.rstrip('/')
    return urlunsplit((scheme, parsed.netloc, path, '', ''))


def _require_secret(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise StorageConfigurationError(f'{field_name} must be text')
    if value == '':
        raise StorageConfigurationError(f'{field_name} is required')
    if any(character in value for character in '\x00\r\n'):
        raise StorageConfigurationError(f'{field_name} must not contain control characters')
    return value


def _require_positive_integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise StorageConfigurationError(f'{field_name} must be a positive integer')
    return value


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise StorageConfigurationError(f'{field_name} must be a boolean')
    return value
