import pytest

from ada.data.core import DataPartition, DataSource, DataSourceView
from ada.data.sources import (
    DataSourceBindingError,
    PiSourceProvider,
    TimePartitionGranularity,
    build_current_source_registry,
)


def test_current_registry_covers_current_bound_sources_but_not_unmaterialized_fabrica_kpis() -> (
    None
):
    registry = build_current_source_registry(pi_source=PiSourceProvider.PI_WEB_API)

    assert DataSource.FABRICA_KPIS not in registry.sources
    assert set(registry.sources) == set(DataSource) - {DataSource.FABRICA_KPIS}


def test_pi_web_api_provider_binds_interpolated_latest_daily_monthly() -> None:
    registry = build_current_source_registry(pi_source=PiSourceProvider.PI_WEB_API)
    interpolated = registry.get(DataSource.PI_INTERPOLATED)

    assert interpolated.definition.key.namespace == ('pi', 'web-api')
    assert interpolated.definition.key.name == 'interpolated'
    latest = interpolated.get_partition(DataPartition.LATEST)
    daily = interpolated.get_partition(DataPartition.DAILY)
    monthly = interpolated.get_partition(DataPartition.MONTHLY)
    assert latest.materialization == 'latest'
    assert latest.time_partition_granularity is None
    assert daily.materialization == 'daily'
    assert daily.time_partition_granularity is TimePartitionGranularity.DAY
    assert monthly.materialization == 'monthly'
    assert monthly.time_partition_granularity is TimePartitionGranularity.MONTH
    assert daily.timestamp_column == 'timestamp_utc'


def test_recorded_has_daily_and_monthly_but_never_latest() -> None:
    registry = build_current_source_registry(pi_source=PiSourceProvider.PI_WEB_API)
    recorded = registry.get(DataSource.PI_RECORDED)

    assert recorded.definition.key.namespace == ('pi', 'web-api')
    assert set(recorded.partitions) == {DataPartition.DAILY, DataPartition.MONTHLY}
    with pytest.raises(DataSourceBindingError, match='does not support partition: latest'):
        registry.get_view(DataSourceView(DataSource.PI_RECORDED, DataPartition.LATEST))


def test_notpii_provider_changes_only_physical_pi_namespace() -> None:
    registry = build_current_source_registry(pi_source=PiSourceProvider.NOTPII)

    assert registry.get(DataSource.PI_INTERPOLATED).definition.key.namespace == ('pi', 'not_pii')
    assert registry.get(DataSource.PI_RECORDED).definition.key.namespace == ('pi', 'not_pii')


def test_pi_provider_must_be_explicit_and_typed() -> None:
    with pytest.raises(TypeError, match='pi_source must be PiSourceProvider'):
        build_current_source_registry(pi_source='notpii')  # type: ignore[arg-type]


def test_non_pi_bindings_are_independent_of_pi_provider() -> None:
    web_api = build_current_source_registry(pi_source=PiSourceProvider.PI_WEB_API)
    notpii = build_current_source_registry(pi_source=PiSourceProvider.NOTPII)

    for source in web_api.sources:
        if source in {DataSource.PI_INTERPOLATED, DataSource.PI_RECORDED}:
            continue
        assert web_api.get(source) == notpii.get(source)


def test_non_pi_sources_keep_source_and_partition_separate() -> None:
    registry = build_current_source_registry(pi_source=PiSourceProvider.PI_WEB_API)

    dispatch = registry.get(DataSource.DISPATCH_STD_SHIFT_STATE)
    shift = dispatch.get_partition(DataPartition.SHIFT)
    assert dispatch.definition.key.namespace == ('dispatch',)
    assert dispatch.definition.key.name == 'std_shift_state'
    assert shift.materialization == 'shift'
    assert shift.shift_column == 'shift_id'

    remanentes = registry.get(DataSource.REMANENTES_STOCKS)
    assert remanentes.get_partition(DataPartition.LATEST).materialization == 'latest'

    fabrica = registry.get(DataSource.FABRICA_PLANES)
    assert fabrica.definition.key.namespace == ('fabrica',)
    assert fabrica.definition.key.name == 'planes'
    assert fabrica.get_partition(DataPartition.DAILY).materialization == 'day'
    assert fabrica.get_partition(DataPartition.WEEKLY).materialization == 'weekly'
    assert fabrica.definition.route_segments == ('fabrica', 'planes')
    assert fabrica.definition.get_materialization('day').resolved_route_segments == ('daily',)
    assert fabrica.definition.get_materialization('weekly').resolved_route_segments == ('weekly',)
