from ada.processes.fabrica.catalog import build_catalog
from ada.processes.fabrica.catalog.kpis import KPI_DATASETS, FabricaKpiId
from ada.processes.fabrica.catalog.plans import PLAN_METRICS, PLAN_PARTITIONS
from atlanticus.data_producers.fabrica import FabricaPlanPartition


def test_plan_catalog_preserves_validated_source_values() -> None:
    assert {item.key: item.source_value for item in PLAN_PARTITIONS} == {
        FabricaPlanPartition.DAY: 'DAY',
        FabricaPlanPartition.WEEKLY: '7LDB',
    }
    assert len(PLAN_METRICS) == 13


def test_kpi_catalog_starts_without_requested_datasets() -> None:
    assert tuple(FabricaKpiId) == ()
    assert KPI_DATASETS == ()


def test_catalog_preserves_stream_sources() -> None:
    catalog = build_catalog()
    assert tuple(item.stream_key for item in catalog) == ('planes', 'kpis')
    assert catalog[0].source_prefix == 'planes_fabrica'
    assert catalog[1].source_prefix == 'MLP/kpi_fabrica/kpi_fabrica'
