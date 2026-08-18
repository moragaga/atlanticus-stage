from enum import StrEnum, unique

from atlanticus.data_producers.fabrica import KpiDatasetDefinition


@unique
class FabricaKpiId(StrEnum):
    pass


KPI_DATASETS: tuple[KpiDatasetDefinition, ...] = ()
