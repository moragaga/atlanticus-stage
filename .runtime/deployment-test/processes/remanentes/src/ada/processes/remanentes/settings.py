from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from ada.processes.remanentes.errors import RemanentesProcessConfigurationError
from atlanticus.configuration import ConfigurationVariableSpec, ResolvedConfiguration
from atlanticus.connectivity.storage import (
    StorageConnectionStringCredential,
    StorageSettings,
)
from atlanticus.data_producers.remanentes import RemanentesStorageConnection

REMANENTES_STORAGE_SUFFIX = 'REMANENTES'
DEFAULT_REMANENTES_IDLE_SECONDS = 30
DEFAULT_REMANENTES_SOURCE_TIMEZONE = 'America/Santiago'


@dataclass(frozen=True, slots=True)
class RemanentesSettings:
    connection: RemanentesStorageConnection
    source_timezone_name: str
    idle_seconds: int = DEFAULT_REMANENTES_IDLE_SECONDS

    @classmethod
    def from_configuration(cls, configuration: ResolvedConfiguration) -> 'RemanentesSettings':
        if not isinstance(configuration, ResolvedConfiguration):
            raise RemanentesProcessConfigurationError(
                'configuration must be a ResolvedConfiguration'
            )
        timezone_name = configuration.require('REMANENTES_SOURCE_TIMEZONE').strip()
        try:
            ZoneInfo(timezone_name)
        except Exception:
            raise RemanentesProcessConfigurationError(
                'REMANENTES_SOURCE_TIMEZONE must be a valid IANA timezone'
            ) from None
        try:
            connection = RemanentesStorageConnection(
                settings=StorageSettings(
                    credential=StorageConnectionStringCredential(
                        connection_string=configuration.require(
                            f'STORAGE_ACCOUNT_CONNECTION_STRING_{REMANENTES_STORAGE_SUFFIX}'
                        )
                    )
                ),
                container_name=configuration.require(
                    f'STORAGE_ACCOUNT_CONTAINER_NAME_{REMANENTES_STORAGE_SUFFIX}'
                ),
            )
        except Exception as error:
            raise RemanentesProcessConfigurationError(str(error)) from error
        return cls(
            connection=connection,
            source_timezone_name=timezone_name,
            idle_seconds=_positive_integer(
                configuration.require('REMANENTES_IDLE_SECONDS'),
                'REMANENTES_IDLE_SECONDS',
            ),
        )


def configuration_specs() -> tuple[ConfigurationVariableSpec, ...]:
    return (
        ConfigurationVariableSpec(key='APPLICATION'),
        ConfigurationVariableSpec(key='VOLUMEN_PATH'),
        ConfigurationVariableSpec(
            key=f'STORAGE_ACCOUNT_CONNECTION_STRING_{REMANENTES_STORAGE_SUFFIX}',
            sensitive=True,
        ),
        ConfigurationVariableSpec(
            key=f'STORAGE_ACCOUNT_CONTAINER_NAME_{REMANENTES_STORAGE_SUFFIX}'
        ),
        ConfigurationVariableSpec(
            key='REMANENTES_SOURCE_TIMEZONE', default=DEFAULT_REMANENTES_SOURCE_TIMEZONE
        ),
        ConfigurationVariableSpec(
            key='REMANENTES_IDLE_SECONDS', default=str(DEFAULT_REMANENTES_IDLE_SECONDS)
        ),
        ConfigurationVariableSpec(key='ATLANTICUS_AZURE_OBSERVABILITY_MODE', default='off'),
        ConfigurationVariableSpec(key='ATLANTICUS_AZURE_OBSERVABILITY_PROFILE', required=False),
        ConfigurationVariableSpec(
            key='APPLICATION_INSIGHTS_CONNECTION_STRING', required=False, sensitive=True
        ),
    )


def _positive_integer(value: str | int, field_name: str) -> int:
    try:
        parsed = int(value)
    except TypeError, ValueError:
        raise RemanentesProcessConfigurationError(
            f'{field_name} must be an integer greater than zero'
        ) from None
    if parsed <= 0:
        raise RemanentesProcessConfigurationError(
            f'{field_name} must be an integer greater than zero'
        )
    return parsed
