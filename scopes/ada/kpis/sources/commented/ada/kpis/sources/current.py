# Declara los bindings actuales de PI, Dispatch, Blockgrade, Remanentes y planes de Fábrica.
from __future__ import annotations

from ada.kpis.core import KpiPartition, KpiSource
from ada.kpis.sources.bindings import (
    KpiPartitionBinding,
    KpiSourceBinding,
    KpiSourceRegistry,
    TimePartitionGranularity,
)
from ada.kpis.sources.pi import PiSourceProvider
from atlanticus.datasets.layouts import SingleArtifactLayout
from atlanticus.datasets.models import DatasetDefinition, DatasetKey, MaterializationDefinition

_PI_NAMESPACES = {
    PiSourceProvider.PI_WEB_API: ('pi', 'web-api'),
    PiSourceProvider.NOTPII: ('pi', 'not_pii'),
}


def build_current_source_registry(*, pi_source: PiSourceProvider) -> KpiSourceRegistry:
    if not isinstance(pi_source, PiSourceProvider):
        raise TypeError('pi_source must be PiSourceProvider')
    pi_namespace = _PI_NAMESPACES[pi_source]
    bindings = {
        KpiSource.PI_INTERPOLATED: _pi_binding(
            source=KpiSource.PI_INTERPOLATED,
            namespace=pi_namespace,
            name='interpolated',
            include_latest=True,
        ),
        KpiSource.PI_RECORDED: _pi_binding(
            source=KpiSource.PI_RECORDED,
            namespace=pi_namespace,
            name='recorded',
            include_latest=False,
        ),
        KpiSource.DISPATCH_TIEMPOS_MLP: _shift_binding(
            KpiSource.DISPATCH_TIEMPOS_MLP,
            namespace=('dispatch',),
            name='tiempos_mlp',
        ),
        KpiSource.DISPATCH_STD_SHIFT_LOADS: _shift_binding(
            KpiSource.DISPATCH_STD_SHIFT_LOADS,
            namespace=('dispatch',),
            name='std_shift_loads',
        ),
        KpiSource.DISPATCH_STD_SHIFT_STATE: _shift_binding(
            KpiSource.DISPATCH_STD_SHIFT_STATE,
            namespace=('dispatch',),
            name='std_shift_state',
        ),
        KpiSource.DISPATCH_STD_TRUCK: _latest_binding(
            KpiSource.DISPATCH_STD_TRUCK,
            namespace=('dispatch',),
            name='std_truck',
        ),
        KpiSource.DISPATCH_STD_SHIFT_GRADE: _shift_binding(
            KpiSource.DISPATCH_STD_SHIFT_GRADE,
            namespace=('dispatch',),
            name='std_shift_grade',
        ),
        KpiSource.DISPATCH_STD_SHIFT_LOADS_2: _shift_binding(
            KpiSource.DISPATCH_STD_SHIFT_LOADS_2,
            namespace=('dispatch',),
            name='std_shift_loads_2',
        ),
        KpiSource.DISPATCH_STD_SHIFT_DUMPS: _shift_binding(
            KpiSource.DISPATCH_STD_SHIFT_DUMPS,
            namespace=('dispatch',),
            name='std_shift_dumps',
        ),
        KpiSource.BLOCKGRADE_MMS_BLOCKGRADE_DETAILS_BUCKET: _shift_binding(
            KpiSource.BLOCKGRADE_MMS_BLOCKGRADE_DETAILS_BUCKET,
            namespace=('blockgrade',),
            name='mms_blockgrade_details_bucket',
        ),
        KpiSource.REMANENTES_EXTRAIBLES: _latest_binding(
            KpiSource.REMANENTES_EXTRAIBLES,
            namespace=('remanentes',),
            name='extraibles',
        ),
        KpiSource.REMANENTES_NO_EXTRAIBLES: _latest_binding(
            KpiSource.REMANENTES_NO_EXTRAIBLES,
            namespace=('remanentes',),
            name='no_extraibles',
        ),
        KpiSource.REMANENTES_STOCKS: _latest_binding(
            KpiSource.REMANENTES_STOCKS,
            namespace=('remanentes',),
            name='stocks',
        ),
        KpiSource.FABRICA_PLANES: _fabrica_planes_binding(),
    }
    return KpiSourceRegistry(bindings)


def _pi_binding(
    *,
    source: KpiSource,
    namespace: tuple[str, ...],
    name: str,
    include_latest: bool,
) -> KpiSourceBinding:
    materializations = []
    partitions: dict[KpiPartition, KpiPartitionBinding] = {}
    if include_latest:
        materializations.append(MaterializationDefinition(name='latest', layout=SingleArtifactLayout()))
        partitions[KpiPartition.LATEST] = KpiPartitionBinding(
            partition=KpiPartition.LATEST,
            materialization='latest',
            timestamp_column='timestamp_utc',
        )
    materializations.extend(
        (
            MaterializationDefinition(
                name='daily',
                layout=SingleArtifactLayout(),
                partition_dimensions=('year', 'month', 'day'),
            ),
            MaterializationDefinition(
                name='monthly',
                layout=SingleArtifactLayout(),
                partition_dimensions=('year', 'month'),
            ),
        )
    )
    partitions[KpiPartition.DAILY] = KpiPartitionBinding(
        partition=KpiPartition.DAILY,
        materialization='daily',
        time_partition_granularity=TimePartitionGranularity.DAY,
        timestamp_column='timestamp_utc',
    )
    partitions[KpiPartition.MONTHLY] = KpiPartitionBinding(
        partition=KpiPartition.MONTHLY,
        materialization='monthly',
        time_partition_granularity=TimePartitionGranularity.MONTH,
        timestamp_column='timestamp_utc',
    )
    return KpiSourceBinding(
        source=source,
        definition=DatasetDefinition(
            key=DatasetKey(namespace=namespace, name=name),
            materializations=tuple(materializations),
        ),
        partitions=partitions,
    )


def _shift_binding(
    source: KpiSource,
    *,
    namespace: tuple[str, ...],
    name: str,
) -> KpiSourceBinding:
    definition = DatasetDefinition(
        key=DatasetKey(namespace=namespace, name=name),
        materializations=(
            MaterializationDefinition(
                name='shift',
                layout=SingleArtifactLayout(),
                partition_dimensions=('year', 'month', 'day', 'turn'),
            ),
        ),
    )
    return KpiSourceBinding(
        source=source,
        definition=definition,
        partitions={
            KpiPartition.SHIFT: KpiPartitionBinding(
                partition=KpiPartition.SHIFT,
                materialization='shift',
                shift_column='shift_id',
            )
        },
    )


def _latest_binding(
    source: KpiSource,
    *,
    namespace: tuple[str, ...],
    name: str,
) -> KpiSourceBinding:
    definition = DatasetDefinition(
        key=DatasetKey(namespace=namespace, name=name),
        materializations=(MaterializationDefinition(name='latest', layout=SingleArtifactLayout()),),
    )
    return KpiSourceBinding(
        source=source,
        definition=definition,
        partitions={
            KpiPartition.LATEST: KpiPartitionBinding(
                partition=KpiPartition.LATEST,
                materialization='latest',
            )
        },
    )


def _fabrica_planes_binding() -> KpiSourceBinding:
    definition = DatasetDefinition(
        key=DatasetKey(namespace=('fabrica',), name='planes'),
        route_segments=('fabrica', 'planes'),
        materializations=(
            MaterializationDefinition(
                name='day',
                layout=SingleArtifactLayout(),
                route_segments=('daily',),
            ),
            MaterializationDefinition(
                name='weekly',
                layout=SingleArtifactLayout(),
                route_segments=('weekly',),
            ),
        ),
    )
    return KpiSourceBinding(
        source=KpiSource.FABRICA_PLANES,
        definition=definition,
        partitions={
            KpiPartition.DAILY: KpiPartitionBinding(
                partition=KpiPartition.DAILY,
                materialization='day',
            ),
            KpiPartition.WEEKLY: KpiPartitionBinding(
                partition=KpiPartition.WEEKLY,
                materialization='weekly',
            ),
        },
    )
