from __future__ import annotations

import re

from ada.processes.fabrica.catalog.kpis import KPI_DATASETS
from ada.processes.fabrica.catalog.plans import PLAN_METRICS, PLAN_PARTITIONS
from atlanticus.data_producers.fabrica import (
    FabricaKpiStreamDefinition,
    FabricaPlanStreamDefinition,
    FabricaStreamDefinition,
)


def build_catalog() -> tuple[FabricaStreamDefinition, ...]:
    return (
        FabricaPlanStreamDefinition(
            source_prefix='planes_fabrica',
            source_filename_pattern=re.compile(
                r'(^|.*/)planes_fabrica_(?P<file_timestamp>\d{14})\.parquet$'
            ),
            output_route_segment='planes',
            partitions=PLAN_PARTITIONS,
            metrics=PLAN_METRICS,
        ),
        FabricaKpiStreamDefinition(
            source_prefix='MLP/kpi_fabrica/kpi_fabrica',
            source_filename_pattern=re.compile(
                r'(^|.*/)kpi_fabrica_(?P<file_timestamp>\d{14})\.parquet$'
            ),
            output_route_segment='kpis',
            datasets=KPI_DATASETS,
        ),
    )
