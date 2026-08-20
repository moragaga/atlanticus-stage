"""Identidad global resuelta desde el ambiente del contenedor."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from atlanticus.kernel import ENVIRONMENT_VARIABLE, Environment, InvalidEnvironmentError
from atlanticus.observability.models import ATLANTICUS_OBSERVABILITY_FILE_LOGS_ENABLED_VARIABLE
from atlanticus.runtime.errors import RuntimeConfigurationError
from atlanticus.runtime.storage import (
    resolve_application_root,
    resolve_runtime_root,
    validate_path_segment,
)

APPLICATION_VARIABLE = 'APPLICATION'
VOLUME_PATH_VARIABLE = 'VOLUMEN_PATH'
_TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})
_FALSE_VALUES = frozenset({'0', 'false', 'no', 'off'})


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    """Configuración común que no contiene secretos específicos de servicios."""

    environment: Environment
    application: str
    volume_path: Path
    observability_file_logs_enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.environment, Environment):
            raise TypeError('environment must be an Environment')
        validate_path_segment(self.application, name='application')
        if not isinstance(self.volume_path, Path):
            raise TypeError('volume_path must be a Path')
        if not self.volume_path.is_absolute():
            raise RuntimeConfigurationError('VOLUMEN_PATH must be an absolute path')
        if not isinstance(self.observability_file_logs_enabled, bool):
            raise TypeError('observability_file_logs_enabled must be a bool')

    @classmethod
    def from_sources(
        cls,
        *,
        cli_environment: str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> RuntimeConfiguration:
        """Resuelve variables sin normalizar silenciosamente el ambiente."""

        source_values = os.environ if environ is None else environ
        if not isinstance(source_values, Mapping):
            raise TypeError('environ must be a mapping')
        env_value = source_values.get(ENVIRONMENT_VARIABLE)
        if env_value is not None and not isinstance(env_value, str):
            raise TypeError(f'{ENVIRONMENT_VARIABLE} must be a string')
        if cli_environment is not None and not isinstance(cli_environment, str):
            raise TypeError('cli_environment must be a string')
        if env_value is not None and cli_environment is not None and env_value != cli_environment:
            raise RuntimeConfigurationError(
                f'conflicting environment values: {ENVIRONMENT_VARIABLE}={env_value!r} '
                f'and --environment={cli_environment!r}'
            )
        resolved_environment = cli_environment if cli_environment is not None else env_value
        try:
            environment = Environment.from_value(resolved_environment)
        except InvalidEnvironmentError as error:
            raise RuntimeConfigurationError(str(error)) from error

        application = _required_value(source_values, APPLICATION_VARIABLE)
        volume_path = Path(_required_value(source_values, VOLUME_PATH_VARIABLE))
        if not volume_path.is_absolute():
            raise RuntimeConfigurationError('VOLUMEN_PATH must be an absolute path')
        return cls(
            environment=environment,
            application=application,
            volume_path=volume_path,
            observability_file_logs_enabled=_optional_bool(
                source_values,
                ATLANTICUS_OBSERVABILITY_FILE_LOGS_ENABLED_VARIABLE,
                default=True,
            ),
        )

    @property
    def application_root(self) -> Path:
        return resolve_application_root(self.volume_path, application=self.application)

    @property
    def runtime_root(self) -> Path:
        return resolve_runtime_root(self.volume_path, application=self.application)


def _required_value(values: Mapping[str, str], name: str) -> str:
    raw_value = values.get(name)
    if raw_value is not None and not isinstance(raw_value, str):
        raise TypeError(f'{name} must be a string')
    if raw_value is None or not raw_value.strip():
        raise RuntimeConfigurationError(f'required environment variable {name} is missing')
    if raw_value != raw_value.strip():
        raise RuntimeConfigurationError(f'{name} must not contain surrounding whitespace')
    return raw_value


def _optional_bool(values: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw_value = values.get(name)
    if raw_value is None:
        return default
    if not isinstance(raw_value, str):
        raise TypeError(f'{name} must be a string')
    normalized = raw_value.lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise RuntimeConfigurationError(f'{name} must contain a valid boolean value')
