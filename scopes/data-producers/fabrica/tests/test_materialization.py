import re
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from atlanticus.data_producers.fabrica import (
    FabricaMaterializer,
    FabricaPlanPartition,
    FabricaPlanStreamDefinition,
    FabricaSourceBlob,
    FabricaStorageSource,
    FabricaValueKind,
    PlanMetricDefinition,
    PlanPartitionDefinition,
)
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime


class _Source(FabricaStorageSource):
    def __init__(self, *, definition, tmp_path: Path) -> None:
        self.definition = definition
        self._tmp_path = tmp_path
        self.table = pa.Table.from_pydict(
            {
                'timestamp': ['2026-08-18T10:00:00Z'],
                'id_kpi': ['A'],
                'valor': ['10'],
                'nivel': ['DAY'],
                'timestamp_ejecucion': ['2026-08-18T10:01:00Z'],
                'particion': ['1'],
            }
        )

    def download(self, *, blob_name: str) -> str:
        path = self._tmp_path / blob_name.replace('/', '_')
        path.write_bytes(b'x')
        return str(path)

    def read_selected_columns(self, *, path: str, metric_ids: tuple[str, ...]):
        return self.table


def _definition() -> FabricaPlanStreamDefinition:
    return FabricaPlanStreamDefinition(
        source_prefix='planes_fabrica',
        source_filename_pattern=re.compile(r'planes_fabrica_(?P<file_timestamp>\d{14})\.parquet$'),
        output_route_segment='planes',
        partitions=(
            PlanPartitionDefinition(
                key=FabricaPlanPartition.DAY,
                source_value='DAY',
                route_segment='daily',
            ),
        ),
        metrics=(
            PlanMetricDefinition(
                id_kpi='A',
                metric_key='a',
                value_kind=FabricaValueKind.NUMBER,
                partitions=(FabricaPlanPartition.DAY,),
            ),
        ),
    )


def _blob(name: str) -> FabricaSourceBlob:
    return FabricaSourceBlob(
        name=name,
        source_file_timestamp_utc=datetime(2026, 8, 18, 10, tzinfo=UTC),
        size=1,
        etag='etag',
        last_modified_utc=datetime(2026, 8, 18, 10, tzinfo=UTC),
    )


def test_materializer_reads_existing_dataset_through_runtime_contract(tmp_path) -> None:
    definition = _definition()
    source = _Source(definition=definition, tmp_path=tmp_path)
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / 'datasets'))
    materializer = FabricaMaterializer(source=source, runtime=runtime, definition=definition)

    first = materializer.materialize(source_blob=_blob('first.parquet'))
    assert first.partitions_changed == 1

    source.table = pa.Table.from_pydict(
        {
            'timestamp': ['2026-08-18T11:00:00Z'],
            'id_kpi': ['A'],
            'valor': ['11'],
            'nivel': ['DAY'],
            'timestamp_ejecucion': ['2026-08-18T11:01:00Z'],
            'particion': ['1'],
        }
    )
    second = materializer.materialize(source_blob=_blob('second.parquet'))

    assert second.partitions_changed == 1


def test_kpi_materializer_writes_one_wide_dataset_per_configured_level(tmp_path) -> None:
    from atlanticus.data_producers.fabrica import (
        FabricaKpiLevel,
        FabricaKpiStreamDefinition,
        KpiDatasetDefinition,
        KpiMetricDefinition,
    )

    oee_stmg = KpiMetricDefinition(
        id_kpi='OEE_STMG',
        metric_key='oee_stmg',
        value_kind=FabricaValueKind.NUMBER,
    )
    oee_transporte = KpiMetricDefinition(
        id_kpi='OEE_TRANSPORTE',
        metric_key='oee_transporte',
        value_kind=FabricaValueKind.NUMBER,
    )
    definition = FabricaKpiStreamDefinition(
        source_prefix='MLP/kpi_fabrica/kpi_fabrica',
        source_filename_pattern=re.compile(r'kpi_fabrica_(?P<file_timestamp>\d{14})\.parquet$'),
        output_route_segment='kpis',
        datasets=(
            KpiDatasetDefinition(
                name='daily',
                level=FabricaKpiLevel.DAY,
                route_segment='daily',
                metrics=(oee_stmg, oee_transporte),
            ),
            KpiDatasetDefinition(
                name='weekly',
                level=FabricaKpiLevel.SEVEN_LAST_DAYS,
                route_segment='weekly',
                metrics=(oee_stmg,),
            ),
        ),
    )
    source = _Source(definition=definition, tmp_path=tmp_path)
    source.table = pa.Table.from_pydict(
        {
            'timestamp': ['2026-08-18T10:00:00Z'] * 4,
            'id_kpi': ['OEE_STMG', 'OEE_TRANSPORTE', 'OEE_STMG', 'OEE_TRANSPORTE'],
            'valor': ['91', '87', '90', '999'],
            'nivel': ['DAY', 'DAY', '7LD', '7LD'],
            'timestamp_ejecucion': ['2026-08-18T10:01:00Z'] * 4,
            'particion': ['202608'] * 4,
        }
    )
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / 'datasets'))
    materializer = FabricaMaterializer(source=source, runtime=runtime, definition=definition)

    result = materializer.materialize(source_blob=_blob('kpi_fabrica_20260818100000.parquet'))

    assert tuple(item.partition_key for item in result.publications) == ('daily', 'weekly')
    assert result.source_row_count == 3
    daily = pq.read_table(tmp_path / 'datasets/fabrica/kpis/daily/data.parquet')
    weekly = pq.read_table(tmp_path / 'datasets/fabrica/kpis/weekly/data.parquet')
    assert daily.column_names == ['timestamp', 'oee_stmg', 'oee_transporte']
    assert weekly.column_names == ['timestamp', 'oee_stmg']
