# Proceso Series: lee Historian, selecciona timestamps exactos y publica una proyección compacta.
# Lee y valida la proyección KPI que queda congelada durante el job.

from __future__ import annotations

from dataclasses import dataclass

from ada.kpis.delivery import (
    KPI_CONFIGURATION_ID,
    KPI_CONFIGURATION_PARTITION_ID,
    KpiDeliveryConfiguration,
    KpiDeliveryValidationError,
)
from ada.processes.kpis_timeseries_delivery.errors import (
    KpiTimeseriesDeliveryConfigurationError,
)
from atlanticus.connectivity.cosmos import CosmosClient

# Constante interna o contractual centralizada para evitar literales dispersos.
KPI_CONFIGURATION_CONTAINER_NAME = 'configuration'


@dataclass(slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesConfigurationRepository:
    client: CosmosClient

    def read(self) -> KpiDeliveryConfiguration:
        document = self.client.find_item(
            container_name=KPI_CONFIGURATION_CONTAINER_NAME,
            item_id=KPI_CONFIGURATION_ID,
            partition_key=KPI_CONFIGURATION_PARTITION_ID,
        )
        if document is None:
            raise KpiTimeseriesDeliveryConfigurationError(
                'KPI configuration projection was not found'
            )
        try:
            return KpiDeliveryConfiguration.from_document(document)
        except KpiDeliveryValidationError as error:
            raise KpiTimeseriesDeliveryConfigurationError(str(error)) from error
