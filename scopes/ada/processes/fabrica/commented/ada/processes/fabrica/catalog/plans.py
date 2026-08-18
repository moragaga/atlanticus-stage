# Espejo comentado del proceso ADA Fábrica. La lógica ejecutable es idéntica al source productivo; este archivo existe para revisión en español.
from atlanticus.data_producers.fabrica import (
    FabricaPlanPartition,
    FabricaValueKind,
    PlanMetricDefinition,
    PlanPartitionDefinition,
)

PLAN_PARTITIONS = (
    PlanPartitionDefinition(
        key=FabricaPlanPartition.DAY,
        source_value='DAY',
        route_segment='daily',
    ),
    PlanPartitionDefinition(
        key=FabricaPlanPartition.WEEKLY,
        source_value='7LDB',
        route_segment='weekly',
    ),
)

PLAN_METRICS = (
    PlanMetricDefinition(
        id_kpi='MOVIMIENTO_MINA',
        metric_key='movimiento_mina',
        value_kind=FabricaValueKind.NUMBER,
        partitions=(FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY),
    ),
    PlanMetricDefinition(
        id_kpi='EXTRACCION_MINA',
        metric_key='extraccion_mina',
        value_kind=FabricaValueKind.NUMBER,
        partitions=(FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY),
    ),
    PlanMetricDefinition(
        id_kpi='REMANEJO',
        metric_key='remanejo',
        value_kind=FabricaValueKind.NUMBER,
        partitions=(FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY),
    ),
    PlanMetricDefinition(
        id_kpi='EXTRACCION_MINA_F9SE',
        metric_key='extraccion_mina_f9se',
        value_kind=FabricaValueKind.NUMBER,
        partitions=(FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY),
    ),
    PlanMetricDefinition(
        id_kpi='EXTRACCION_MINA_F10N',
        metric_key='extraccion_mina_f10n',
        value_kind=FabricaValueKind.NUMBER,
        partitions=(FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY),
    ),
    PlanMetricDefinition(
        id_kpi='EXTRACCION_MINA_F11W',
        metric_key='extraccion_mina_f11w',
        value_kind=FabricaValueKind.NUMBER,
        partitions=(FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY),
    ),
    PlanMetricDefinition(
        id_kpi='EXTRACCION_MINA_F12N',
        metric_key='extraccion_mina_f12n',
        value_kind=FabricaValueKind.NUMBER,
        partitions=(FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY),
    ),
    PlanMetricDefinition(
        id_kpi='TRANSPORTADO_STMG',
        metric_key='transportado_stmg',
        value_kind=FabricaValueKind.NUMBER,
        partitions=(FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY),
    ),
    PlanMetricDefinition(
        id_kpi='LEY_CU_MINA_CONCILIADO',
        metric_key='ley_cu_mina_conciliado',
        value_kind=FabricaValueKind.NUMBER,
        partitions=(FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY),
    ),
    PlanMetricDefinition(
        id_kpi='PLANTA_CONCENTRADORA_TRATAMIENTO',
        metric_key='planta_concentradora_tratamiento',
        value_kind=FabricaValueKind.NUMBER,
        partitions=(FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY),
    ),
    PlanMetricDefinition(
        id_kpi='PLANTA_CONCENTRADORA_RECUPERACION_CU',
        metric_key='planta_concentradora_recuperacion_cu',
        value_kind=FabricaValueKind.NUMBER,
        partitions=(FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY),
    ),
    PlanMetricDefinition(
        id_kpi='PLANTA_CONCENTRADORA_CU_FINO_PRODUCIDO',
        metric_key='planta_concentradora_cu_fino_producido',
        value_kind=FabricaValueKind.NUMBER,
        partitions=(FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY),
    ),
    PlanMetricDefinition(
        id_kpi='PLANTA_CONCENTRADORA_MO_FINO_PRODUCIDO',
        metric_key='planta_concentradora_mo_fino_producido',
        value_kind=FabricaValueKind.NUMBER,
        partitions=(FabricaPlanPartition.DAY, FabricaPlanPartition.WEEKLY),
    ),
)
