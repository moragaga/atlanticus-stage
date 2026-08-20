# API pública del proceso: reúne contratos, composición, configuración y publicación sin esconder dependencias.
from ada.processes.kpis_delivery.bootstrap import load_configuration, run
from ada.processes.kpis_delivery.composition import KpiDeliveryComposition, build_composition
from ada.processes.kpis_delivery.contracts import (
    KpiDeliveryBindingsReader,
    KpiLatestReader,
    KpiLatestSnapshotPublisher,
)
from ada.processes.kpis_delivery.errors import (
    KpiDeliveryConfigurationError,
    KpiDeliveryRepositoryError,
)
from ada.processes.kpis_delivery.job import (
    KpiLatestDeliveryIterationResult,
    KpiLatestDeliveryJob,
)
from ada.processes.kpis_delivery.models import (
    KpiLatestPublication,
    KpiLatestPublicationStatus,
)
from ada.processes.kpis_delivery.repository import KpiLatestSnapshotRepository
from ada.processes.kpis_delivery.settings import KpiDeliveryProcessSettings, configuration_specs

__version__ = '0.1.0'

__all__ = [
    'KpiDeliveryBindingsReader',
    'KpiDeliveryComposition',
    'KpiDeliveryConfigurationError',
    'KpiDeliveryProcessSettings',
    'KpiDeliveryRepositoryError',
    'KpiLatestDeliveryIterationResult',
    'KpiLatestDeliveryJob',
    'KpiLatestPublication',
    'KpiLatestPublicationStatus',
    'KpiLatestReader',
    'KpiLatestSnapshotPublisher',
    'KpiLatestSnapshotRepository',
    '__version__',
    'build_composition',
    'configuration_specs',
    'load_configuration',
    'run',
]
