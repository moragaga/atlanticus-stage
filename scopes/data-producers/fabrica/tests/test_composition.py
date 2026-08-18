import re

from atlanticus.connectivity.storage import StorageSasCredential, StorageSettings
from atlanticus.data_producers.fabrica import (
    FabricaKpiStreamDefinition,
    FabricaPlanPartition,
    FabricaPlanStreamDefinition,
    FabricaStorageConnection,
    FabricaValueKind,
    PlanMetricDefinition,
    PlanPartitionDefinition,
    build_fabrica_data_producer,
)
from atlanticus.kernel import Environment
from atlanticus.runtime import RuntimeConfiguration


def _connection(account_url: str, container_name: str) -> FabricaStorageConnection:
    return FabricaStorageConnection(
        settings=StorageSettings(
            credential=StorageSasCredential(
                account_url=account_url,
                sas_token='sv=1',
            )
        ),
        container_name=container_name,
    )


def test_empty_kpi_catalog_does_not_build_storage_or_materializer(tmp_path) -> None:
    planes = FabricaPlanStreamDefinition(
        source_prefix='planes_fabrica',
        source_filename_pattern=re.compile(
            r'(^|.*/)planes_fabrica_(?P<file_timestamp>\d{14})\.parquet$'
        ),
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
                id_kpi='PLAN_A',
                metric_key='plan_a',
                value_kind=FabricaValueKind.NUMBER,
                partitions=(FabricaPlanPartition.DAY,),
            ),
        ),
    )
    kpis = FabricaKpiStreamDefinition(
        source_prefix='MLP/kpi_fabrica/kpi_fabrica',
        source_filename_pattern=re.compile(
            r'(^|.*/)kpi_fabrica_(?P<file_timestamp>\d{14})\.parquet$'
        ),
        output_route_segment='kpis',
        datasets=(),
    )

    components = build_fabrica_data_producer(
        runtime_configuration=RuntimeConfiguration(
            environment=Environment.from_value('local'),
            application='ada',
            volume_path=tmp_path,
        ),
        definitions=(planes, kpis),
        connections={
            'planes': _connection('https://planes.example.test', 'planes'),
            'kpis': _connection('https://kpis.example.test', 'kpis'),
        },
        idle_seconds=5,
    )

    assert set(components.storages) == {'planes'}
    assert tuple(item.definition.stream_key for item in components.materializers) == ('planes',)
