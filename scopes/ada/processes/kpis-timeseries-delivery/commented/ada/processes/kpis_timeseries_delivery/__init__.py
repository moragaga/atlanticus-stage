# Proceso Series: lee Historian, selecciona timestamps exactos y publica una proyección compacta.
# Expone únicamente el contrato público estable del paquete.

from ada.processes.kpis_timeseries_delivery.bootstrap import load_configuration, run
from ada.processes.kpis_timeseries_delivery.composition import (
    KpiTimeseriesDeliveryComposition,
    build_composition,
)
from ada.processes.kpis_timeseries_delivery.configuration import (
    KPI_CONFIGURATION_CONTAINER_NAME,
    KpiTimeseriesConfigurationRepository,
)
from ada.processes.kpis_timeseries_delivery.errors import (
    KpiTimeseriesDeliveryConfigurationError,
    KpiTimeseriesDeliveryError,
    KpiTimeseriesDeliveryHistoryError,
    KpiTimeseriesDeliveryRepositoryError,
)
from ada.processes.kpis_timeseries_delivery.history import KpiTimeseriesHistoryRepository
from ada.processes.kpis_timeseries_delivery.job import (
    KPI_TIMESERIES_STEP_SECONDS,
    KpiTimeseriesDeliveryIterationResult,
    KpiTimeseriesDeliveryIterationStatus,
    KpiTimeseriesDeliveryJob,
)
from ada.processes.kpis_timeseries_delivery.models import (
    KpiTimeseriesCheckpoint,
    KpiTimeseriesPublication,
    KpiTimeseriesPublicationStatus,
)
from ada.processes.kpis_timeseries_delivery.repository import (
    KPI_TIMESERIES_DATA_DOCUMENT_TYPE,
    KPI_TIMESERIES_DELIVERY_CONTAINER_NAME,
    KpiTimeseriesSnapshotRepository,
)
from ada.processes.kpis_timeseries_delivery.settings import (
    KpiTimeseriesDeliveryProcessSettings,
    configuration_specs,
)
from ada.processes.kpis_timeseries_delivery.state import (
    KpiHistorianWatermarkStore,
    KpiTimeseriesDeliveryCheckpointStore,
)

__version__ = '0.1.0'

__all__ = [
    'KPI_CONFIGURATION_CONTAINER_NAME',
    'KPI_TIMESERIES_DATA_DOCUMENT_TYPE',
    'KPI_TIMESERIES_DELIVERY_CONTAINER_NAME',
    'KPI_TIMESERIES_STEP_SECONDS',
    'KpiHistorianWatermarkStore',
    'KpiTimeseriesCheckpoint',
    'KpiTimeseriesConfigurationRepository',
    'KpiTimeseriesDeliveryCheckpointStore',
    'KpiTimeseriesDeliveryComposition',
    'KpiTimeseriesDeliveryConfigurationError',
    'KpiTimeseriesDeliveryError',
    'KpiTimeseriesDeliveryHistoryError',
    'KpiTimeseriesDeliveryIterationResult',
    'KpiTimeseriesDeliveryIterationStatus',
    'KpiTimeseriesDeliveryJob',
    'KpiTimeseriesDeliveryProcessSettings',
    'KpiTimeseriesDeliveryRepositoryError',
    'KpiTimeseriesHistoryRepository',
    'KpiTimeseriesPublication',
    'KpiTimeseriesPublicationStatus',
    'KpiTimeseriesSnapshotRepository',
    '__version__',
    'build_composition',
    'configuration_specs',
    'load_configuration',
    'run',
]
