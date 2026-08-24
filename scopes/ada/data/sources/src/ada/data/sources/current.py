from __future__ import annotations

from ada.data.core import DataPartition, DataSource
from ada.data.sources.bindings import (
    DataPartitionBinding,
    DataSourceBinding,
    DataSourceRegistry,
    TimePartitionGranularity,
)
from ada.data.sources.pi import PiSourceProvider
from atlanticus.datasets.layouts import SingleArtifactLayout
from atlanticus.datasets.models import DatasetDefinition, DatasetKey, MaterializationDefinition

_PI_NAMESPACES = {
    PiSourceProvider.PI_WEB_API: ('pi', 'web-api'),
    PiSourceProvider.NOTPII: ('pi', 'not_pii'),
}


def build_current_source_registry(*, pi_source: PiSourceProvider) -> DataSourceRegistry:
    if not isinstance(pi_source, PiSourceProvider):
        raise TypeError('pi_source must be PiSourceProvider')
    pi_namespace = _PI_NAMESPACES[pi_source]
    bindings = {
        DataSource.PI_INTERPOLATED: _pi_binding(
            source=DataSource.PI_INTERPOLATED,
            namespace=pi_namespace,
            name='interpolated',
            include_latest=True,
        ),
        DataSource.PI_RECORDED: _pi_binding(
            source=DataSource.PI_RECORDED,
            namespace=pi_namespace,
            name='recorded',
            include_latest=False,
        ),
        DataSource.DISPATCH_TIEMPOS_MLP: _shift_binding(
            DataSource.DISPATCH_TIEMPOS_MLP,
            namespace=('dispatch',),
            name='tiempos_mlp',
        ),
        DataSource.DISPATCH_STD_SHIFT_LOADS: _shift_binding(
            DataSource.DISPATCH_STD_SHIFT_LOADS,
            namespace=('dispatch',),
            name='std_shift_loads',
        ),
        DataSource.DISPATCH_STD_SHIFT_STATE: _shift_binding(
            DataSource.DISPATCH_STD_SHIFT_STATE,
            namespace=('dispatch',),
            name='std_shift_state',
        ),
        DataSource.DISPATCH_STD_TRUCK: _latest_binding(
            DataSource.DISPATCH_STD_TRUCK,
            namespace=('dispatch',),
            name='std_truck',
        ),
        DataSource.DISPATCH_STD_SHIFT_GRADE: _shift_binding(
            DataSource.DISPATCH_STD_SHIFT_GRADE,
            namespace=('dispatch',),
            name='std_shift_grade',
        ),
        DataSource.DISPATCH_STD_SHIFT_LOADS_2: _shift_binding(
            DataSource.DISPATCH_STD_SHIFT_LOADS_2,
            namespace=('dispatch',),
            name='std_shift_loads_2',
        ),
        DataSource.DISPATCH_STD_SHIFT_DUMPS: _shift_binding(
            DataSource.DISPATCH_STD_SHIFT_DUMPS,
            namespace=('dispatch',),
            name='std_shift_dumps',
        ),
        DataSource.BLOCKGRADE_MMS_BLOCKGRADE_DETAILS_BUCKET: _shift_binding(
            DataSource.BLOCKGRADE_MMS_BLOCKGRADE_DETAILS_BUCKET,
            namespace=('blockgrade',),
            name='mms_blockgrade_details_bucket',
        ),
        DataSource.REMANENTES_EXTRAIBLES: _latest_binding(
            DataSource.REMANENTES_EXTRAIBLES,
            namespace=('remanentes',),
            name='extraibles',
        ),
        DataSource.REMANENTES_NO_EXTRAIBLES: _latest_binding(
            DataSource.REMANENTES_NO_EXTRAIBLES,
            namespace=('remanentes',),
            name='no_extraibles',
        ),
        DataSource.REMANENTES_STOCKS: _latest_binding(
            DataSource.REMANENTES_STOCKS,
            namespace=('remanentes',),
            name='stocks',
        ),
        DataSource.FABRICA_PLANES: _fabrica_planes_binding(),
    }
    return DataSourceRegistry(bindings)


def _pi_binding(
    *,
    source: DataSource,
    namespace: tuple[str, ...],
    name: str,
    include_latest: bool,
) -> DataSourceBinding:
    materializations = []
    partitions: dict[DataPartition, DataPartitionBinding] = {}
    if include_latest:
        materializations.append(
            MaterializationDefinition(name='latest', layout=SingleArtifactLayout())
        )
        partitions[DataPartition.LATEST] = DataPartitionBinding(
            partition=DataPartition.LATEST,
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
    partitions[DataPartition.DAILY] = DataPartitionBinding(
        partition=DataPartition.DAILY,
        materialization='daily',
        time_partition_granularity=TimePartitionGranularity.DAY,
        timestamp_column='timestamp_utc',
    )
    partitions[DataPartition.MONTHLY] = DataPartitionBinding(
        partition=DataPartition.MONTHLY,
        materialization='monthly',
        time_partition_granularity=TimePartitionGranularity.MONTH,
        timestamp_column='timestamp_utc',
    )
    return DataSourceBinding(
        source=source,
        definition=DatasetDefinition(
            key=DatasetKey(namespace=namespace, name=name),
            materializations=tuple(materializations),
        ),
        partitions=partitions,
    )


def _shift_binding(
    source: DataSource,
    *,
    namespace: tuple[str, ...],
    name: str,
) -> DataSourceBinding:
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
    return DataSourceBinding(
        source=source,
        definition=definition,
        partitions={
            DataPartition.SHIFT: DataPartitionBinding(
                partition=DataPartition.SHIFT,
                materialization='shift',
                shift_column='shift_id',
            )
        },
    )


def _latest_binding(
    source: DataSource,
    *,
    namespace: tuple[str, ...],
    name: str,
) -> DataSourceBinding:
    definition = DatasetDefinition(
        key=DatasetKey(namespace=namespace, name=name),
        materializations=(MaterializationDefinition(name='latest', layout=SingleArtifactLayout()),),
    )
    return DataSourceBinding(
        source=source,
        definition=definition,
        partitions={
            DataPartition.LATEST: DataPartitionBinding(
                partition=DataPartition.LATEST,
                materialization='latest',
            )
        },
    )


def _fabrica_planes_binding() -> DataSourceBinding:
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
    return DataSourceBinding(
        source=DataSource.FABRICA_PLANES,
        definition=definition,
        partitions={
            DataPartition.DAILY: DataPartitionBinding(
                partition=DataPartition.DAILY,
                materialization='day',
            ),
            DataPartition.WEEKLY: DataPartitionBinding(
                partition=DataPartition.WEEKLY,
                materialization='weekly',
            ),
        },
    )
