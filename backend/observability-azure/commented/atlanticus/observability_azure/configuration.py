"""Configuración explícita de la extensión Azure sin leer secretos por servicio."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

AZURE_OBSERVABILITY_MODE_VARIABLE = 'ATLANTICUS_AZURE_OBSERVABILITY_MODE'
AZURE_OBSERVABILITY_PROFILE_VARIABLE = 'ATLANTICUS_AZURE_OBSERVABILITY_PROFILE'
APPLICATION_INSIGHTS_CONNECTION_STRING_VARIABLE = 'APPLICATION_INSIGHTS_CONNECTION_STRING'


class AzureObservabilityConfigurationError(ValueError):
    """Indica una configuración inválida sin incluir valores secretos."""


class AzureObservabilityMode(StrEnum):
    """Decide si la proyección se omite, previsualiza o exporta."""

    OFF = 'off'
    PREVIEW = 'preview'
    EXPORT = 'export'


class AzureObservabilityProfile(StrEnum):
    """Controla el nivel de detalle permitido para Azure."""

    SLIM = 'slim'
    DIAGNOSTIC = 'diagnostic'


@dataclass(frozen=True, slots=True)
# Mantiene la configuración Azure separada de la observabilidad neutral y nunca expone el secreto en repr.
class AzureObservabilitySettings:
    """Valores acotados de la extensión; la conexión nunca aparece en repr."""

    mode: AzureObservabilityMode = AzureObservabilityMode.OFF
    profile: AzureObservabilityProfile = AzureObservabilityProfile.SLIM
    connection_string: str | None = field(default=None, repr=False, compare=False)
    flush_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        if not isinstance(self.mode, AzureObservabilityMode):
            raise TypeError('mode must be an AzureObservabilityMode')
        if not isinstance(self.profile, AzureObservabilityProfile):
            raise TypeError('profile must be an AzureObservabilityProfile')
        if isinstance(self.flush_timeout_seconds, bool) or not isinstance(
            self.flush_timeout_seconds, int | float
        ):
            raise TypeError('flush_timeout_seconds must be an int or float')
        if not math.isfinite(self.flush_timeout_seconds) or self.flush_timeout_seconds <= 0:
            raise ValueError('flush_timeout_seconds must be greater than zero')
        object.__setattr__(self, 'flush_timeout_seconds', float(self.flush_timeout_seconds))
        if self.connection_string is not None and not isinstance(self.connection_string, str):
            raise TypeError('connection_string must be a string or None')
        if self.mode is not AzureObservabilityMode.EXPORT:
            object.__setattr__(self, 'connection_string', None)
            return
        connection_string = self.connection_string
        if connection_string is None or connection_string == '':
            raise AzureObservabilityConfigurationError(
                f'{APPLICATION_INSIGHTS_CONNECTION_STRING_VARIABLE} is required in export mode'
            )
        object.__setattr__(self, 'connection_string', connection_string)

    # La composición entrega un mapping explícito para evitar lecturas laterales de os.environ.
    @classmethod
    def from_sources(
        cls,
        *,
        environ: Mapping[str, str],
    ) -> AzureObservabilitySettings:
        """Resuelve únicamente las variables globales de la extensión."""

        if not isinstance(environ, Mapping):
            raise TypeError('environ must be a mapping')
        values = environ
        mode = _parse_enum(
            AzureObservabilityMode,
            _read_value(values, AZURE_OBSERVABILITY_MODE_VARIABLE, AzureObservabilityMode.OFF),
            AZURE_OBSERVABILITY_MODE_VARIABLE,
        )
        profile = _parse_enum(
            AzureObservabilityProfile,
            _read_value(
                values,
                AZURE_OBSERVABILITY_PROFILE_VARIABLE,
                AzureObservabilityProfile.SLIM,
            ),
            AZURE_OBSERVABILITY_PROFILE_VARIABLE,
        )
        connection_string = None
        if mode is AzureObservabilityMode.EXPORT:
            connection_string = _read_value(
                values,
                APPLICATION_INSIGHTS_CONNECTION_STRING_VARIABLE,
                None,
            )
        return cls(
            mode=mode,
            profile=profile,
            connection_string=connection_string,
        )

    @property
    def tracing_enabled(self) -> bool:
        return self.mode is not AzureObservabilityMode.OFF and (
            self.profile is AzureObservabilityProfile.DIAGNOSTIC
        )


def _read_value(
    values: Mapping[str, str],
    variable_name: str,
    default: str | None,
) -> str | None:
    value = values.get(variable_name, default)
    if value is not None and not isinstance(value, str):
        raise AzureObservabilityConfigurationError(f'{variable_name} must be a string')
    return value


def _parse_enum(enum_type, value: str | None, variable_name: str):
    if value is None:
        raise AzureObservabilityConfigurationError(f'{variable_name} must be a string')
    try:
        return enum_type(value.strip().lower())
    except ValueError as error:
        allowed = ', '.join(item.value for item in enum_type)
        raise AzureObservabilityConfigurationError(
            f'{variable_name} must be one of: {allowed}'
        ) from error
