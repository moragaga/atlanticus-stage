import re
from datetime import UTC, datetime

from atlanticus.data_producers.fabrica import (
    FabricaKpiLevel,
    FabricaKpiStreamDefinition,
    KpiDatasetDefinition,
    parse_source_file_timestamp,
)


def _definition() -> FabricaKpiStreamDefinition:
    return FabricaKpiStreamDefinition(
        source_prefix='MLP/kpi',
        source_filename_pattern=re.compile(r'kpi_fabrica_(?P<file_timestamp>\d{14})\.parquet$'),
        output_route_segment='kpis',
        datasets=(
            KpiDatasetDefinition(
                name='daily',
                level=FabricaKpiLevel.DAY,
                route_segment='daily',
                metrics=(),
            ),
        ),
    )


def test_source_filename_timestamp_is_normalized_to_utc() -> None:
    assert parse_source_file_timestamp(
        definition=_definition(), blob_name='kpi_fabrica_20260810173031.parquet'
    ) == datetime(2026, 8, 10, 17, 30, 31, tzinfo=UTC)


def test_source_day_prefix_uses_partition_timezone() -> None:
    definition = FabricaKpiStreamDefinition(
        source_prefix='/MLP/kpi/',
        source_filename_pattern=re.compile(r'kpi_(?P<file_timestamp>\d{14})\.parquet$'),
        output_route_segment='kpis',
        datasets=(),
    )
    assert definition.source_day_prefix(datetime(2026, 8, 11, 1, 0, tzinfo=UTC)) == (
        'MLP/kpi/year=2026/month=08/day=10/'
    )
