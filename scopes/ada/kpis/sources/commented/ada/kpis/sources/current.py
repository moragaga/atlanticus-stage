# Espejo pedagógico: el código ejecutable es idéntico; los comentarios explican responsabilidades y fronteras.
# Este catálogo enlaza KpiSource con datasets Atlanticus sin exponer el productor físico a las rules.
# PI_SOURCE se resuelve una vez al iniciar el proceso y selecciona en bloque los datasets PI correspondientes.
from __future__ import annotations

from ada.kpis.core import KpiSource
from ada.kpis.sources.bindings import (
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
    # Una sola selección física gobierna INTERPOLATED y RECORDED durante toda la ejecución.
    pi_namespace = _PI_NAMESPACES[pi_source]
    bindings = {
        KpiSource.PI_INTERPOLATED: _time_binding(
            source=KpiSource.PI_INTERPOLATED,
            namespace=pi_namespace,
            name='interpolated',
            time_materialization='daily',
            granularity=TimePartitionGranularity.DAY,
        ),
        KpiSource.PI_RECORDED: _time_binding(
            source=KpiSource.PI_RECORDED,
            namespace=pi_namespace,
            name='recorded',
            time_materialization='monthly',
            granularity=TimePartitionGranularity.MONTH,
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
        KpiSource.DISPATCH_STD_TRUCK: _snapshot_binding(
            KpiSource.DISPATCH_STD_TRUCK,
            namespace=('dispatch',),
            name='std_truck',
            materialization='latest',
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
        KpiSource.REMANENTES_EXTRAIBLES: _snapshot_binding(
            KpiSource.REMANENTES_EXTRAIBLES,
            namespace=('remanentes',),
            name='extraibles',
            materialization='latest',
            timestamp_column='timestamp',
        ),
        KpiSource.REMANENTES_NO_EXTRAIBLES: _snapshot_binding(
            KpiSource.REMANENTES_NO_EXTRAIBLES,
            namespace=('remanentes',),
            name='no_extraibles',
            materialization='latest',
            timestamp_column='timestamp',
        ),
        KpiSource.REMANENTES_STOCKS: _snapshot_binding(
            KpiSource.REMANENTES_STOCKS,
            namespace=('remanentes',),
            name='stocks',
            materialization='latest',
            timestamp_column='timestamp',
        ),
        KpiSource.FABRICA_PLANES_DAILY: _snapshot_binding(
            KpiSource.FABRICA_PLANES_DAILY,
            namespace=('fabrica',),
            name='planes',
            materialization='daily',
            timestamp_column='timestamp',
            sibling_materializations=('weekly',),
        ),
        KpiSource.FABRICA_PLANES_WEEKLY: _snapshot_binding(
            KpiSource.FABRICA_PLANES_WEEKLY,
            namespace=('fabrica',),
            name='planes',
            materialization='weekly',
            timestamp_column='timestamp',
            sibling_materializations=('daily',),
        ),
    }
    return KpiSourceRegistry(bindings)


def _time_binding(
    *,
    source: KpiSource,
    namespace: tuple[str, ...],
    name: str,
    time_materialization: str,
    granularity: TimePartitionGranularity,
) -> KpiSourceBinding:
    partition_dimensions = (
        ('year', 'month', 'day')
        if granularity is TimePartitionGranularity.DAY
        else ('year', 'month')
    )
    definition = DatasetDefinition(
        key=DatasetKey(namespace=namespace, name=name),
        materializations=(
            MaterializationDefinition(name='latest', layout=SingleArtifactLayout()),
            MaterializationDefinition(
                name=time_materialization,
                layout=SingleArtifactLayout(),
                partition_dimensions=partition_dimensions,
            ),
        ),
    )
    return KpiSourceBinding(
        source=source,
        definition=definition,
        snapshot_materialization='latest',
        time_materialization=time_materialization,
        time_partition_granularity=granularity,
        timestamp_column='timestamp_utc',
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
        shift_materialization='shift',
        shift_column='shift_id',
    )


def _snapshot_binding(
    source: KpiSource,
    *,
    namespace: tuple[str, ...],
    name: str,
    materialization: str,
    timestamp_column: str | None = None,
    sibling_materializations: tuple[str, ...] = (),
) -> KpiSourceBinding:
    names = (materialization, *sibling_materializations)
    definition = DatasetDefinition(
        key=DatasetKey(namespace=namespace, name=name),
        materializations=tuple(
            MaterializationDefinition(name=item, layout=SingleArtifactLayout()) for item in names
        ),
    )
    return KpiSourceBinding(
        source=source,
        definition=definition,
        snapshot_materialization=materialization,
        timestamp_column=timestamp_column,
    )
