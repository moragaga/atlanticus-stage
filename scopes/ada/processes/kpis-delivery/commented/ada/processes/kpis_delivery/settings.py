# Proceso Latest: congela configuración por job, observa watermark fresco y publica sólo cuando corresponde.
# Resuelve settings externos sin exponer nombres internos de contenedores.

from __future__ import annotations

from dataclasses import dataclass

from ada.processes.kpis_delivery.errors import KpiDeliveryConfigurationError
from atlanticus.configuration import ConfigurationVariableSpec, ResolvedConfiguration
from atlanticus.connectivity.cosmos import CosmosConfigurationError, CosmosSettings

# Constante interna o contractual centralizada para evitar literales dispersos.
_DEFAULT_POLL_INTERVAL_SECONDS = 10


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiDeliveryProcessSettings:
    cosmos: CosmosSettings
    poll_interval_seconds: int = _DEFAULT_POLL_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.cosmos, CosmosSettings):
            raise KpiDeliveryConfigurationError('cosmos must be CosmosSettings')
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
        try:
            cosmos = CosmosSettings(
                endpoint=configuration.require('COSMOS_CONSUMPTION_ENDPOINT'),
                key=configuration.require('COSMOS_CONSUMPTION_KEY'),
                database_name=configuration.require('COSMOS_CONSUMPTION_DATABASE_NAME'),
                allow_insecure_http=configuration.environment.is_local,
            )
        except CosmosConfigurationError as error:
            raise KpiDeliveryConfigurationError(str(error)) from error
        return cls(
            cosmos=cosmos,
            poll_interval_seconds=poll_interval_seconds,
        )


# La función mantiene una operación pequeña y verificable de esta frontera.
def configuration_specs() -> tuple[ConfigurationVariableSpec, ...]:
    return (
        ConfigurationVariableSpec(key='APPLICATION'),
        ConfigurationVariableSpec(key='VOLUMEN_PATH'),
        ConfigurationVariableSpec(key='COSMOS_CONSUMPTION_ENDPOINT'),
        ConfigurationVariableSpec(key='COSMOS_CONSUMPTION_KEY', sensitive=True),
        ConfigurationVariableSpec(key='COSMOS_CONSUMPTION_DATABASE_NAME'),
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
