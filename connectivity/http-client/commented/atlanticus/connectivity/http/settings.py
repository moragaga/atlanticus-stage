"""Configuración inmutable y explícita para una conexión HTTP."""

from __future__ import annotations

# Este módulo recibe valores finales desde composición; no conoce variables de entorno ni sufijos.
import math
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from atlanticus.connectivity.http.errors import HttpConfigurationError
from atlanticus.connectivity.http.models import HttpAuthMode

_DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024


# Los secretos quedan fuera del repr y se conservan exactamente; solo la cadena vacía representa ausencia.
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

    def __post_init__(self) -> None:
        auth_mode = _require_auth_mode(self.auth_mode)
        verify_tls = _require_bool(self.verify_tls, 'verify_tls')
        allow_insecure_http = _require_bool(self.allow_insecure_http, 'allow_insecure_http')
        object.__setattr__(self, 'verify_tls', verify_tls)
        object.__setattr__(self, 'allow_insecure_http', allow_insecure_http)
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

        bearer_token = _optional_credential(self.bearer_token, 'bearer_token')
        username = _optional_credential(self.username, 'username')
        password = _optional_credential(self.password, 'password')
        _validate_authentication(
            auth_mode=auth_mode,
            bearer_token=bearer_token,
            username=username,
            password=password,
        )
        object.__setattr__(self, 'bearer_token', bearer_token)
        object.__setattr__(self, 'username', username)
        object.__setattr__(self, 'password', password)


# La URL sí es configuración estructural: se normaliza para que los endpoints relativos sean deterministas.
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


# Credenciales no se recortan: espacios pueden ser parte legítima de un usuario o password.
def _optional_credential(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HttpConfigurationError(f'{field_name} must be text or null')
    if value == '':
        return None
    return value


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise HttpConfigurationError(f'{field_name} must be text')
    normalized = value.strip()
    if not normalized:
        raise HttpConfigurationError(f'{field_name} is required')
    return normalized


# Cada modo admite únicamente su conjunto de credenciales para evitar combinaciones ambiguas.
def _validate_authentication(
    *,
    auth_mode: HttpAuthMode,
    bearer_token: str | None,
    username: str | None,
    password: str | None,
) -> None:
    if auth_mode == HttpAuthMode.NONE:
        if any(value is not None for value in (bearer_token, username, password)):
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
