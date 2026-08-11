"""Configuración inmutable y explícita para una conexión HTTP."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from atlanticus.connectivity.http.errors import HttpConfigurationError
from atlanticus.connectivity.http.models import HttpAuthMode

_DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_SUFFIX_PATTERN = re.compile(r'^[A-Z0-9]+(?:_[A-Z0-9]+)*$')
_TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})
_FALSE_VALUES = frozenset({'0', 'false', 'no', 'off'})


@dataclass(frozen=True, slots=True)
class HttpConfigurationKeys:
    """Claves exactas resueltas para una conexión HTTP concreta."""

    base_url: str
    auth_mode: str
    bearer_token: str
    username: str
    password: str
    connect_timeout_seconds: str
    read_timeout_seconds: str
    write_timeout_seconds: str
    pool_timeout_seconds: str
    max_response_bytes: str
    verify_tls: str
    allow_insecure_http: str


@dataclass(frozen=True, slots=True)
class HttpSettings:
    """Destino, autenticación, timeouts y límite de respuesta ya resueltos."""

    base_url: str
    auth_mode: HttpAuthMode
    bearer_token: str | None = field(default=None, repr=False)
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 30.0
    write_timeout_seconds: float = 30.0
    pool_timeout_seconds: float = 5.0
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES
    verify_tls: bool = True
    allow_insecure_http: bool = False
    suffix: str | None = None

    def __post_init__(self) -> None:
        auth_mode = _require_auth_mode(self.auth_mode)
        verify_tls = _require_bool(self.verify_tls, 'verify_tls')
        allow_insecure_http = _require_bool(self.allow_insecure_http, 'allow_insecure_http')
        object.__setattr__(self, 'verify_tls', verify_tls)
        object.__setattr__(self, 'allow_insecure_http', allow_insecure_http)
        object.__setattr__(self, 'suffix', _normalize_configuration_suffix(self.suffix))
        object.__setattr__(
            self,
            'base_url',
            _normalize_base_url(
                self.base_url,
                allow_insecure_http=allow_insecure_http,
            ),
        )

        for name in (
            'connect_timeout_seconds',
            'read_timeout_seconds',
            'write_timeout_seconds',
            'pool_timeout_seconds',
        ):
            object.__setattr__(self, name, _require_positive_number(getattr(self, name), name))
        object.__setattr__(
            self,
            'max_response_bytes',
            _require_positive_integer(self.max_response_bytes, 'max_response_bytes'),
        )

        bearer_token = _optional_text(self.bearer_token)
        username = _optional_text(self.username)
        password = _optional_text(self.password)
        _validate_authentication(
            auth_mode=auth_mode,
            bearer_token=bearer_token,
            username=username,
            password=password,
        )
        object.__setattr__(self, 'bearer_token', bearer_token)
        object.__setattr__(self, 'username', username)
        object.__setattr__(self, 'password', password)

    @classmethod
    def from_mapping(
        cls,
        *,
        values: Mapping[str, Any],
        suffix: str | None = None,
    ) -> HttpSettings:
        """Construye settings desde valores resueltos por otra capa."""

        if not isinstance(values, Mapping):
            raise HttpConfigurationError('values must be a mapping')
        normalized_suffix = _normalize_configuration_suffix(suffix)
        keys = _build_http_configuration_keys(suffix=normalized_suffix)
        missing = tuple(
            key
            for key in (keys.base_url, keys.auth_mode)
            if _optional_mapping_text(values, key) is None
        )
        if missing:
            raise HttpConfigurationError('Missing HTTP configuration keys: ' + ', '.join(missing))

        return cls(
            base_url=_require_mapping_text(values, keys.base_url),
            auth_mode=_parse_auth_mode(_require_mapping_text(values, keys.auth_mode)),
            bearer_token=_optional_mapping_text(values, keys.bearer_token),
            username=_optional_mapping_text(values, keys.username),
            password=_optional_mapping_text(values, keys.password),
            connect_timeout_seconds=_parse_mapping_number(
                values,
                keys.connect_timeout_seconds,
                default=5.0,
            ),
            read_timeout_seconds=_parse_mapping_number(
                values,
                keys.read_timeout_seconds,
                default=30.0,
            ),
            write_timeout_seconds=_parse_mapping_number(
                values,
                keys.write_timeout_seconds,
                default=30.0,
            ),
            pool_timeout_seconds=_parse_mapping_number(
                values,
                keys.pool_timeout_seconds,
                default=5.0,
            ),
            max_response_bytes=_parse_mapping_integer(
                values,
                keys.max_response_bytes,
                default=_DEFAULT_MAX_RESPONSE_BYTES,
            ),
            verify_tls=_parse_mapping_bool(values, keys.verify_tls, default=True),
            allow_insecure_http=_parse_mapping_bool(
                values,
                keys.allow_insecure_http,
                default=False,
            ),
            suffix=normalized_suffix,
        )


def _build_http_configuration_keys(*, suffix: str | None = None) -> HttpConfigurationKeys:
    normalized_suffix = _normalize_configuration_suffix(suffix)

    def key(name: str) -> str:
        base = f'HTTP_{name}'
        return base if normalized_suffix is None else f'{base}_{normalized_suffix}'

    return HttpConfigurationKeys(
        base_url=key('BASE_URL'),
        auth_mode=key('AUTH_MODE'),
        bearer_token=key('BEARER_TOKEN'),
        username=key('USERNAME'),
        password=key('PASSWORD'),
        connect_timeout_seconds=key('CONNECT_TIMEOUT_SECONDS'),
        read_timeout_seconds=key('READ_TIMEOUT_SECONDS'),
        write_timeout_seconds=key('WRITE_TIMEOUT_SECONDS'),
        pool_timeout_seconds=key('POOL_TIMEOUT_SECONDS'),
        max_response_bytes=key('MAX_RESPONSE_BYTES'),
        verify_tls=key('VERIFY_TLS'),
        allow_insecure_http=key('ALLOW_INSECURE_HTTP'),
    )


def _normalize_configuration_suffix(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HttpConfigurationError('HTTP configuration suffix must be text')
    normalized = value.strip().strip('_').upper()
    if not normalized:
        return None
    if _SUFFIX_PATTERN.fullmatch(normalized) is None:
        raise HttpConfigurationError(
            'HTTP configuration suffix must contain only letters, numbers and single underscores'
        )
    return normalized


def _normalize_base_url(value: str, *, allow_insecure_http: bool) -> str:
    normalized = _require_text(value, 'base_url')
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError:
        raise HttpConfigurationError('base_url must be a valid HTTP or HTTPS URL') from None
    scheme = parsed.scheme.lower()
    if scheme not in {'http', 'https'} or not parsed.netloc or parsed.hostname is None:
        raise HttpConfigurationError('base_url must be an absolute HTTP or HTTPS URL')
    if port is not None and not 1 <= port <= 65535:
        raise HttpConfigurationError('base_url must contain a valid port')
    if parsed.username is not None or parsed.password is not None:
        raise HttpConfigurationError('base_url must not contain credentials')
    if parsed.query or parsed.fragment:
        raise HttpConfigurationError('base_url must not contain query parameters or fragments')
    if scheme == 'http' and not allow_insecure_http:
        raise HttpConfigurationError('HTTP requires allow_insecure_http=True')
    path = f'{parsed.path.rstrip("/")}/'
    return urlunsplit((scheme, parsed.netloc, path, '', ''))


def _require_auth_mode(value: Any) -> HttpAuthMode:
    if not isinstance(value, HttpAuthMode):
        raise HttpConfigurationError('auth_mode must be HttpAuthMode')
    return value


def _parse_auth_mode(value: str) -> HttpAuthMode:
    normalized = value.strip().lower()
    try:
        return HttpAuthMode(normalized)
    except ValueError:
        raise HttpConfigurationError('auth_mode must be none, bearer or basic') from None


def _parse_mapping_number(
    values: Mapping[str, Any],
    key: str,
    *,
    default: float,
) -> float:
    value = _optional_mapping_text(values, key)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        raise HttpConfigurationError(f'{key} must be a number greater than zero') from None
    if not math.isfinite(parsed) or parsed <= 0:
        raise HttpConfigurationError(f'{key} must be a number greater than zero')
    return parsed


def _parse_mapping_integer(
    values: Mapping[str, Any],
    key: str,
    *,
    default: int,
) -> int:
    value = _optional_mapping_text(values, key)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        raise HttpConfigurationError(f'{key} must be an integer greater than zero') from None
    if str(parsed) != value and not (value.startswith('+') and str(parsed) == value[1:]):
        raise HttpConfigurationError(f'{key} must be an integer greater than zero')
    return _require_positive_integer(parsed, key)


def _parse_mapping_bool(
    values: Mapping[str, Any],
    key: str,
    *,
    default: bool,
) -> bool:
    value = values.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        raise HttpConfigurationError(f'{key} must be a boolean')
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise HttpConfigurationError(f'{key} must be a boolean')


def _require_positive_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise HttpConfigurationError(f'{field_name} must be a number greater than zero')
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise HttpConfigurationError(f'{field_name} must be a number greater than zero')
    return parsed


def _require_positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HttpConfigurationError(f'{field_name} must be an integer greater than zero')
    return value


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise HttpConfigurationError(f'{field_name} must be a boolean')
    return value


def _optional_mapping_text(values: Mapping[str, Any], key: str) -> str | None:
    return _optional_text(values.get(key))


def _require_mapping_text(values: Mapping[str, Any], key: str) -> str:
    return _require_text(_optional_mapping_text(values, key), key)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HttpConfigurationError('HTTP text configuration values must be strings')
    normalized = value.strip()
    return normalized or None


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise HttpConfigurationError(f'{field_name} must be text')
    normalized = value.strip()
    if not normalized:
        raise HttpConfigurationError(f'{field_name} is required')
    return normalized


def _validate_authentication(
    *,
    auth_mode: HttpAuthMode,
    bearer_token: str | None,
    username: str | None,
    password: str | None,
) -> None:
    if auth_mode == HttpAuthMode.NONE:
        if any((bearer_token, username, password)):
            raise HttpConfigurationError('auth_mode none does not accept credentials')
        return
    if auth_mode == HttpAuthMode.BEARER:
        if bearer_token is None:
            raise HttpConfigurationError('bearer_token is required for bearer authentication')
        if username is not None or password is not None:
            raise HttpConfigurationError('bearer authentication does not accept basic credentials')
        if any(character.isspace() for character in bearer_token):
            raise HttpConfigurationError('bearer_token must not contain whitespace')
        return
    if username is None or password is None:
        raise HttpConfigurationError('username and password are required for basic authentication')
    if bearer_token is not None:
        raise HttpConfigurationError('basic authentication does not accept bearer_token')
    if ':' in username:
        raise HttpConfigurationError('username must not contain a colon')
    if any(character in '\r\n' for character in username + password):
        raise HttpConfigurationError('basic credentials must not contain line breaks')
