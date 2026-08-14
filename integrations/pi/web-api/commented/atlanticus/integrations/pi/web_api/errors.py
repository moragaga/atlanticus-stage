# Jerarquía de errores propia de PI Web API.
# Aísla al process de las excepciones concretas de la capa HTTP.

from __future__ import annotations

from typing import Any


class PiWebApiError(Exception):
    pass


class PiWebApiConfigurationError(PiWebApiError):
    pass


class PiWebApiRequestError(PiWebApiError):
    pass


class PiWebApiConnectionError(PiWebApiError):
    pass


class PiWebApiTimeoutError(PiWebApiConnectionError):
    def __init__(self, *, phase: str) -> None:
        self.phase = _require_text(phase, 'phase')
        super().__init__(f'PI Web API {self.phase} timeout')


class PiWebApiResponseError(PiWebApiError):
    pass


class PiWebApiStatusError(PiWebApiResponseError):
    def __init__(self, *, status_code: int) -> None:
        if (
            isinstance(status_code, bool)
            or not isinstance(status_code, int)
            or not 100 <= status_code <= 599
        ):
            raise TypeError('status_code must be an integer between 100 and 599')
        self.status_code = status_code
        super().__init__(f'PI Web API request failed with status {status_code}')


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f'{field_name} must be text')
    normalized = value.strip()
    if not normalized:
        raise TypeError(f'{field_name} must be non-empty text')
    return normalized
