# La configuración PI compone HttpSettings y no repite URL, usuario, password ni TLS.
# Los límites son defaults runtime configurables, no constantes rígidas del wheel.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from atlanticus.connectivity.http import HttpAuthMode, HttpSettings
from atlanticus.integrations.pi.web_api.errors import PiWebApiConfigurationError


@dataclass(frozen=True, slots=True)
class PiWebApiLimits:
    points_max_paths: int = 100
    interpolated_max_web_ids: int = 100
    recorded_max_web_ids: int = 100

    def __post_init__(self) -> None:
        for field_name in (
            'points_max_paths',
            'interpolated_max_web_ids',
            'recorded_max_web_ids',
        ):
            object.__setattr__(
                self,
                field_name,
                _require_positive_integer(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class PiWebApiSettings:
    pi_server: str
    http: HttpSettings = field(repr=False)
    limits: PiWebApiLimits = field(default_factory=PiWebApiLimits)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'pi_server', _require_pi_server(self.pi_server))
        if not isinstance(self.http, HttpSettings):
            raise PiWebApiConfigurationError('http must be HttpSettings')
        if self.http.auth_mode is not HttpAuthMode.BASIC:
            raise PiWebApiConfigurationError('PI Web API requires basic HTTP authentication')
        if not isinstance(self.limits, PiWebApiLimits):
            raise PiWebApiConfigurationError('limits must be PiWebApiLimits')


def _require_pi_server(value: Any) -> str:
    if not isinstance(value, str):
        raise PiWebApiConfigurationError('pi_server must be text')
    if not value:
        raise PiWebApiConfigurationError('pi_server is required')
    if value != value.strip():
        raise PiWebApiConfigurationError('pi_server must not contain surrounding whitespace')
    if '\\' in value or '/' in value:
        raise PiWebApiConfigurationError('pi_server must not contain path separators')
    return value


def _require_positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PiWebApiConfigurationError(f'{field_name} must be an integer greater than zero')
    return value
