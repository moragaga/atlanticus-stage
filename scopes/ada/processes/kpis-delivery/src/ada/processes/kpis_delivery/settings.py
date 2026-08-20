from __future__ import annotations

from dataclasses import dataclass

from ada.processes.kpis_delivery.errors import KpiDeliveryConfigurationError
from atlanticus.configuration import ConfigurationVariableSpec, ResolvedConfiguration
from atlanticus.connectivity.cosmos import CosmosConfigurationError, CosmosSettings

_DEFAULT_POLL_INTERVAL_SECONDS = 10


@dataclass(frozen=True, slots=True)
class KpiDeliveryProcessSettings:
    cosmos: CosmosSettings
    container_name: str
    poll_interval_seconds: int = _DEFAULT_POLL_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.cosmos, CosmosSettings):
            raise KpiDeliveryConfigurationError('cosmos must be CosmosSettings')
        container_name = _required_text(
            self.container_name,
            variable='COSMOS_CONSUMPTION_CONTAINER_NAME',
        )
        object.__setattr__(self, 'container_name', container_name)
        if (
            not isinstance(self.poll_interval_seconds, int)
            or isinstance(self.poll_interval_seconds, bool)
            or self.poll_interval_seconds <= 0
        ):
            raise KpiDeliveryConfigurationError(
                'KPI_DELIVERY_POLL_INTERVAL_SECONDS must be an integer greater than zero'
            )

    @classmethod
    def from_configuration(cls, configuration: ResolvedConfiguration) -> KpiDeliveryProcessSettings:
        if not isinstance(configuration, ResolvedConfiguration):
            raise KpiDeliveryConfigurationError('configuration must be a ResolvedConfiguration')
        poll_interval_seconds = configuration.get_int('KPI_DELIVERY_POLL_INTERVAL_SECONDS')
        if poll_interval_seconds is None:
            raise KpiDeliveryConfigurationError(
                'KPI_DELIVERY_POLL_INTERVAL_SECONDS must contain an integer value'
            )
        allow_insecure_http = configuration.get_bool(
            'COSMOS_CONSUMPTION_ALLOW_INSECURE_HTTP',
            False,
        )
        if allow_insecure_http is None:
            allow_insecure_http = False
        try:
            cosmos = CosmosSettings(
                endpoint=configuration.require('COSMOS_CONSUMPTION_ENDPOINT'),
                key=configuration.require('COSMOS_CONSUMPTION_KEY'),
                database_name=configuration.require('COSMOS_CONSUMPTION_DATABASE_NAME'),
                allow_insecure_http=allow_insecure_http,
            )
        except CosmosConfigurationError as error:
            raise KpiDeliveryConfigurationError(str(error)) from error
        return cls(
            cosmos=cosmos,
            container_name=configuration.require('COSMOS_CONSUMPTION_CONTAINER_NAME'),
            poll_interval_seconds=poll_interval_seconds,
        )


def configuration_specs() -> tuple[ConfigurationVariableSpec, ...]:
    return (
        ConfigurationVariableSpec(key='APPLICATION'),
        ConfigurationVariableSpec(key='VOLUMEN_PATH'),
        ConfigurationVariableSpec(key='COSMOS_CONSUMPTION_ENDPOINT'),
        ConfigurationVariableSpec(key='COSMOS_CONSUMPTION_KEY', sensitive=True),
        ConfigurationVariableSpec(key='COSMOS_CONSUMPTION_DATABASE_NAME'),
        ConfigurationVariableSpec(key='COSMOS_CONSUMPTION_CONTAINER_NAME'),
        ConfigurationVariableSpec(
            key='COSMOS_CONSUMPTION_ALLOW_INSECURE_HTTP',
            default='false',
        ),
        ConfigurationVariableSpec(
            key='KPI_DELIVERY_POLL_INTERVAL_SECONDS',
            default=str(_DEFAULT_POLL_INTERVAL_SECONDS),
        ),
        ConfigurationVariableSpec(key='ATLANTICUS_AZURE_OBSERVABILITY_MODE', default='off'),
        ConfigurationVariableSpec(key='ATLANTICUS_AZURE_OBSERVABILITY_PROFILE', required=False),
        ConfigurationVariableSpec(
            key='APPLICATION_INSIGHTS_CONNECTION_STRING',
            required=False,
            sensitive=True,
        ),
    )


def _required_text(value: object, *, variable: str) -> str:
    if not isinstance(value, str) or not value:
        raise KpiDeliveryConfigurationError(f'{variable} must be a non-empty string')
    if value != value.strip():
        raise KpiDeliveryConfigurationError(f'{variable} must not contain surrounding whitespace')
    return value
