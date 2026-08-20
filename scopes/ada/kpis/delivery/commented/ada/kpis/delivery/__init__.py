# API pública mínima del módulo de KPI Latest Delivery.
from ada.kpis.delivery.errors import KpiDeliveryError, KpiDeliveryValidationError
from ada.kpis.delivery.models import (
    KpiDeliveryBinding,
    KpiDeliveryManifest,
    KpiDeliverySnapshot,
    KpiDeliveryStatus,
    KpiDeliveryValue,
)
from ada.kpis.delivery.projection import (
    KPI_LATEST_DELIVERY_ID,
    KPI_LATEST_PARTITION_ID,
    KPI_LATEST_SCHEMA_VERSION,
    calculate_kpi_latest_revision,
    project_kpi_latest,
)

__version__ = '0.1.0'

__all__ = [
    'KPI_LATEST_DELIVERY_ID',
    'KPI_LATEST_PARTITION_ID',
    'KPI_LATEST_SCHEMA_VERSION',
    'KpiDeliveryBinding',
    'KpiDeliveryError',
    'KpiDeliveryManifest',
    'KpiDeliverySnapshot',
    'KpiDeliveryStatus',
    'KpiDeliveryValidationError',
    'KpiDeliveryValue',
    '__version__',
    'calculate_kpi_latest_revision',
    'project_kpi_latest',
]
