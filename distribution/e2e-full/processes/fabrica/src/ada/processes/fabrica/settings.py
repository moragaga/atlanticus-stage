from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit

from ada.processes.fabrica.errors import FabricaProcessConfigurationError
from atlanticus.configuration import ConfigurationVariableSpec, ResolvedConfiguration
from atlanticus.connectivity.storage import StorageSasCredential, StorageSettings
from atlanticus.data_producers.fabrica import FabricaStorageConnection

PLANES_STORAGE_SUFFIX = 'FABRICA_PLANES'
KPIS_STORAGE_SUFFIX = 'FABRICA_KPIS'
DEFAULT_FABRICA_IDLE_SECONDS = 5


@dataclass(frozen=True, slots=True)
class FabricaSettings:
    connections: Mapping[str, FabricaStorageConnection]
    idle_seconds: int = DEFAULT_FABRICA_IDLE_SECONDS

    def __post_init__(self) -> None:
        connections = dict(self.connections)
        if set(connections) != {'planes', 'kpis'}:
            raise FabricaProcessConfigurationError(
                'connections must contain the named planes and kpis Storage connections'
            )
        if not all(isinstance(value, FabricaStorageConnection) for value in connections.values()):
            raise FabricaProcessConfigurationError(
                'connections must contain FabricaStorageConnection values'
            )
        object.__setattr__(self, 'connections', MappingProxyType(connections))
        object.__setattr__(
            self,
            'idle_seconds',
            _positive_integer(self.idle_seconds, 'FABRICA_IDLE_SECONDS'),
        )

    @classmethod
    def from_configuration(cls, configuration: ResolvedConfiguration) -> FabricaSettings:
        if not isinstance(configuration, ResolvedConfiguration):
            raise FabricaProcessConfigurationError('configuration must be a ResolvedConfiguration')
        return cls(
            connections={
                'planes': _sas_connection(configuration, suffix=PLANES_STORAGE_SUFFIX),
                'kpis': _sas_connection(configuration, suffix=KPIS_STORAGE_SUFFIX),
            },
            idle_seconds=configuration.require('FABRICA_IDLE_SECONDS'),
        )


def configuration_specs() -> tuple[ConfigurationVariableSpec, ...]:
    return (
        ConfigurationVariableSpec(key='APPLICATION'),
        ConfigurationVariableSpec(key='VOLUMEN_PATH'),
        ConfigurationVariableSpec(
            key=f'STORAGE_ACCOUNT_SAS_URL_{PLANES_STORAGE_SUFFIX}',
            sensitive=True,
        ),
        ConfigurationVariableSpec(
            key=f'STORAGE_ACCOUNT_SAS_TOKEN_{PLANES_STORAGE_SUFFIX}',
            required=False,
            sensitive=True,
        ),
        ConfigurationVariableSpec(
            key=f'STORAGE_ACCOUNT_SAS_URL_{KPIS_STORAGE_SUFFIX}',
            sensitive=True,
        ),
        ConfigurationVariableSpec(
            key=f'STORAGE_ACCOUNT_SAS_TOKEN_{KPIS_STORAGE_SUFFIX}',
            required=False,
            sensitive=True,
        ),
        ConfigurationVariableSpec(
            key='FABRICA_IDLE_SECONDS',
            default=str(DEFAULT_FABRICA_IDLE_SECONDS),
        ),
        ConfigurationVariableSpec(key='ATLANTICUS_AZURE_OBSERVABILITY_MODE', default='off'),
        ConfigurationVariableSpec(key='ATLANTICUS_AZURE_OBSERVABILITY_PROFILE', required=False),
        ConfigurationVariableSpec(
            key='APPLICATION_INSIGHTS_CONNECTION_STRING',
            required=False,
            sensitive=True,
        ),
    )


def _sas_connection(
    configuration: ResolvedConfiguration,
    *,
    suffix: str,
) -> FabricaStorageConnection:
    url_key = f'STORAGE_ACCOUNT_SAS_URL_{suffix}'
    token_key = f'STORAGE_ACCOUNT_SAS_TOKEN_{suffix}'
    raw_url = configuration.require(url_key)
    parsed = urlsplit(raw_url.strip())
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise FabricaProcessConfigurationError(f'{url_key} must be an absolute HTTP or HTTPS URL')
    path_parts = tuple(part for part in parsed.path.split('/') if part)
    if len(path_parts) != 1:
        raise FabricaProcessConfigurationError(f'{url_key} must identify exactly one container')
    query_token = parsed.query.strip().lstrip('?')
    configured_token = str(configuration.values.get(token_key) or '').strip().lstrip('?')
    if query_token and configured_token and query_token != configured_token:
        raise FabricaProcessConfigurationError(
            f'{url_key} and {token_key} contain different SAS tokens'
        )
    token = configured_token or query_token
    if not token:
        raise FabricaProcessConfigurationError(
            f'{token_key} is required when {url_key} has no query'
        )
    account_url = urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))
    try:
        settings = StorageSettings(
            credential=StorageSasCredential(
                account_url=account_url,
                sas_token=token,
                allow_insecure_http=parsed.scheme == 'http',
            )
        )
    except Exception as error:
        raise FabricaProcessConfigurationError(str(error)) from error
    return FabricaStorageConnection(settings=settings, container_name=path_parts[0])


def _positive_integer(value: str | int, field_name: str) -> int:
    try:
        parsed = int(value)
    except TypeError, ValueError:
        raise FabricaProcessConfigurationError(
            f'{field_name} must be an integer greater than zero'
        ) from None
    if parsed <= 0:
        raise FabricaProcessConfigurationError(f'{field_name} must be an integer greater than zero')
    return parsed
