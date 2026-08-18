# Espejo comentado del catálogo KPI ADA. Los IDs y datasets se incorporan únicamente cuando una herramienta los requiere.
from enum import StrEnum, unique

from atlanticus.data_producers.fabrica import KpiDatasetDefinition


@unique
class FabricaKpiId(StrEnum):
    pass


KPI_DATASETS: tuple[KpiDatasetDefinition, ...] = ()
