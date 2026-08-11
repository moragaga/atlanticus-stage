"""Modelos neutrales que no exponen tipos de HTTPX."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_SUPPORTED_METHODS = frozenset({'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'})


class HttpAuthMode(StrEnum):
    """Modos de autenticación incluidos en el contrato inicial."""

    NONE = 'none'
    BEARER = 'bearer'
    BASIC = 'basic'


class HttpTimeoutPhase(StrEnum):
    """Fase exacta en que HTTPX agotó el presupuesto configurado."""

    CONNECT = 'connect'
    READ = 'read'
    WRITE = 'write'
    POOL = 'pool'


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Respuesta acotada cargada completamente en memoria."""

    status_code: int
    method: str
    headers: Mapping[str, str] = field(repr=False)
    content: bytes = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'status_code', _require_status_code(self.status_code))
        object.__setattr__(self, 'method', _require_method(self.method))
        object.__setattr__(self, 'headers', _freeze_headers(self.headers))
        object.__setattr__(self, 'content', _copy_content(self.content))

    def decode_text(self, *, encoding: str = 'utf-8') -> str:
        """Decodifica el cuerpo con una codificación elegida por el consumidor."""

        if not isinstance(encoding, str) or not encoding.strip():
            from atlanticus.connectivity.http.errors import HttpResponseError

            raise HttpResponseError('encoding must be non-empty text')
        try:
            return self.content.decode(encoding)
        except LookupError, UnicodeError:
            from atlanticus.connectivity.http.errors import HttpResponseError

            raise HttpResponseError('HTTP response is not valid text') from None

    def decode_json(self) -> Any:
        """Decodifica JSON UTF-8 sin claves duplicadas ni números no finitos."""

        try:
            text = self.content.decode('utf-8')
            return json.loads(
                text,
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
                parse_float=_parse_finite_float,
            )
        except UnicodeError, ValueError, RecursionError:
            from atlanticus.connectivity.http.errors import HttpResponseError

            raise HttpResponseError('HTTP response is not valid JSON') from None


@dataclass(frozen=True, slots=True)
class HttpStreamResult:
    """Metadatos de una respuesta transferida a un stream del consumidor."""

    status_code: int
    method: str
    bytes_transferred: int
    headers: Mapping[str, str] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'status_code', _require_status_code(self.status_code))
        object.__setattr__(self, 'method', _require_method(self.method))
        object.__setattr__(
            self,
            'bytes_transferred',
            _require_non_negative_integer(self.bytes_transferred, 'bytes_transferred'),
        )
        object.__setattr__(self, 'headers', _freeze_headers(self.headers))


class _InvalidJsonValue(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidJsonValue
        result[key] = value
    return result


def _reject_json_constant(_: str) -> Any:
    raise _InvalidJsonValue


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise _InvalidJsonValue
    return parsed


def _require_status_code(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        raise TypeError('status_code must be an integer between 100 and 599')
    return value


def _require_method(value: Any) -> str:
    if not isinstance(value, str) or value not in _SUPPORTED_METHODS:
        raise TypeError('method must be a supported uppercase HTTP method')
    return value


def _require_non_negative_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(f'{field_name} must be a non-negative integer')
    return value


def _freeze_headers(value: Any) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError('headers must be a mapping')
    normalized: dict[str, str] = {}
    for name, header_value in value.items():
        if not isinstance(name, str) or _HEADER_NAME_PATTERN.fullmatch(name) is None:
            raise TypeError('header names must be valid text')
        if not isinstance(header_value, str) or '\r' in header_value or '\n' in header_value:
            raise TypeError('header values must be text without line breaks')
        normalized[name.lower()] = header_value
    return MappingProxyType(normalized)


def _copy_content(value: Any) -> bytes:
    if not isinstance(value, bytes | bytearray | memoryview):
        raise TypeError('content must be bytes-like')
    return bytes(value)
