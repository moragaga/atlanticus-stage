"""Errores públicos y seguros del transporte HTTP."""

from __future__ import annotations

from typing import Any

from atlanticus.connectivity.http.models import HttpTimeoutPhase

_SUPPORTED_METHODS = frozenset({'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'})


class HttpError(Exception):
    """Error base de ``atlanticus-http``."""


class HttpConfigurationError(HttpError):
    """Indica una configuración ausente, inválida o incompatible."""


class HttpRequestError(HttpError):
    """Indica que una solicitud no cumple el contrato local del cliente."""


class HttpConnectionError(HttpError):
    """Representa un fallo de red o protocolo sin exponer la URL."""


class HttpTimeoutError(HttpError):
    """Representa un timeout clasificado por fase y sin reintento implícito."""

    def __init__(
        self,
        *,
        phase: HttpTimeoutPhase,
        bytes_transferred: int = 0,
    ) -> None:
        if not isinstance(phase, HttpTimeoutPhase):
            raise TypeError('phase must be HttpTimeoutPhase')
        self.phase = phase
        self.bytes_transferred = _require_non_negative_integer(
            bytes_transferred,
            'bytes_transferred',
        )
        super().__init__(f'HTTP {phase.value} timeout')


class HttpStatusError(HttpError):
    """Indica una respuesta fuera del rango exitoso ``2xx``."""

    def __init__(self, *, status_code: int, method: str) -> None:
        if (
            isinstance(status_code, bool)
            or not isinstance(status_code, int)
            or not 100 <= status_code <= 599
        ):
            raise TypeError('status_code must be an integer between 100 and 599')
        if not isinstance(method, str) or method not in _SUPPORTED_METHODS:
            raise TypeError('method must be a supported uppercase HTTP method')
        self.status_code = status_code
        self.method = method
        super().__init__(f'HTTP {method} failed with status {status_code}')


class HttpResponseError(HttpError):
    """Indica que una respuesta no cumple el contrato solicitado."""


class HttpStreamError(HttpError):
    """Indica una descarga interrumpida después de escribir contenido parcial."""

    def __init__(self, *, bytes_transferred: int) -> None:
        self.bytes_transferred = _require_non_negative_integer(
            bytes_transferred,
            'bytes_transferred',
        )
        super().__init__('HTTP response stream failed')


def _require_non_negative_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(f'{field_name} must be a non-negative integer')
    return value
