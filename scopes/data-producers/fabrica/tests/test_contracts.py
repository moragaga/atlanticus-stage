import pytest

from atlanticus.data_producers.fabrica import (
    FabricaContractError,
    FabricaKpiLevel,
    FabricaPlanPartition,
    FabricaValueKind,
    KpiDatasetDefinition,
    KpiMetricDefinition,
    PlanMetricDefinition,
    PlanPartitionDefinition,
    validate_kpi_catalog,
    validate_plan_catalog,
)


def test_plan_metric_keeps_partition_selection() -> None:
    plan = PlanMetricDefinition(
        id_kpi='PLAN_A',
        metric_key='plan_a',
        value_kind=FabricaValueKind.NUMBER,
        partitions=(FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY),
    )
    assert plan.partitions == (FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY)


def test_source_value_is_unique_inside_plan_catalog() -> None:
    with pytest.raises(FabricaContractError, match='source_value'):
        validate_plan_catalog(
            partitions=(
                PlanPartitionDefinition(
                    key=FabricaPlanPartition.DAY, source_value='DAY', route_segment='daily'
                ),
                PlanPartitionDefinition(
                    key=FabricaPlanPartition.WEEKLY,
                    source_value='day',
                    route_segment='weekly',
                ),
            ),
            metrics=(),
        )


def test_kpi_dataset_groups_exact_metrics_for_one_level() -> None:
    oee_stmg = KpiMetricDefinition(
        id_kpi='OEE_STMG',
        metric_key='oee_stmg',
        value_kind=FabricaValueKind.NUMBER,
    )
    daily = KpiDatasetDefinition(
        name='daily',
        level=FabricaKpiLevel.DAY,
        route_segment='daily',
        metrics=(oee_stmg,),
    )
    weekly = KpiDatasetDefinition(
        name='weekly',
        level=FabricaKpiLevel.SEVEN_LAST_DAYS,
        route_segment='weekly',
        metrics=(oee_stmg,),
    )
    validate_kpi_catalog(datasets=(daily, weekly))


def test_same_kpi_must_reuse_same_definition_across_datasets() -> None:
    daily = KpiDatasetDefinition(
        name='daily',
        level=FabricaKpiLevel.DAY,
        route_segment='daily',
        metrics=(
            KpiMetricDefinition(
                id_kpi='OEE_STMG',
                metric_key='oee_stmg',
                value_kind=FabricaValueKind.NUMBER,
            ),
        ),
    )
    weekly = KpiDatasetDefinition(
        name='weekly',
        level=FabricaKpiLevel.SEVEN_LAST_DAYS,
        route_segment='weekly',
        metrics=(
            KpiMetricDefinition(
                id_kpi='OEE_STMG',
                metric_key='different_key',
                value_kind=FabricaValueKind.NUMBER,
            ),
        ),
    )
    with pytest.raises(FabricaContractError, match='same metric definition'):
        validate_kpi_catalog(datasets=(daily, weekly))


def test_kpi_dataset_rejects_duplicate_metric_ids() -> None:
    metric = KpiMetricDefinition(
        id_kpi='OEE_STMG',
        metric_key='oee_stmg',
        value_kind=FabricaValueKind.NUMBER,
    )
    with pytest.raises(FabricaContractError, match='id_kpi'):
        KpiDatasetDefinition(
            name='daily',
            level=FabricaKpiLevel.DAY,
            route_segment='daily',
            metrics=(metric, metric),
        )
