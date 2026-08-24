import pytest

from ada.kpis.core import KpiPartition, KpiSource, KpiSourceView
from ada.kpis.sources import (
    KpiSourceBindingError,
    PiSourceProvider,
    TimePartitionGranularity,
    build_current_source_registry,
)


def test_current_registry_covers_current_bound_sources_but_not_unmaterialized_fabrica_kpis() -> (
    None
):
    registry = build_current_source_registry(pi_source=PiSourceProvider.PI_WEB_API)

    assert KpiSource.FABRICA_KPIS not in registry.sources
    assert set(registry.sources) == set(KpiSource) - {KpiSource.FABRICA_KPIS}


def test_pi_web_api_provider_binds_interpolated_latest_daily_monthly() -> None:
    registry = build_current_source_registry(pi_source=PiSourceProvider.PI_WEB_API)
    interpolated = registry.get(KpiSource.PI_INTERPOLATED)

    assert interpolated.definition.key.namespace == ('pi', 'web-api')
    assert interpolated.definition.key.name == 'interpolated'
    latest = interpolated.get_partition(KpiPartition.LATEST)
    daily = interpolated.get_partition(KpiPartition.DAILY)
    monthly = interpolated.get_partition(KpiPartition.MONTHLY)
    assert latest.materialization == 'latest'
    assert latest.time_partition_granularity is None
    assert daily.materialization == 'daily'
    assert daily.time_partition_granularity is TimePartitionGranularity.DAY
    assert monthly.materialization == 'monthly'
    assert monthly.time_partition_granularity is TimePartitionGranularity.MONTH
    assert daily.timestamp_column == 'timestamp_utc'


def test_recorded_has_daily_and_monthly_but_never_latest() -> None:
    registry = build_current_source_registry(pi_source=PiSourceProvider.PI_WEB_API)
    recorded = registry.get(KpiSource.PI_RECORDED)

    assert recorded.definition.key.namespace == ('pi', 'web-api')
    assert set(recorded.partitions) == {KpiPartition.DAILY, KpiPartition.MONTHLY}
    with pytest.raises(KpiSourceBindingError, match='does not support partition: latest'):
        registry.get_view(KpiSourceView(KpiSource.PI_RECORDED, KpiPartition.LATEST))


def test_notpii_provider_changes_only_physical_pi_namespace() -> None:
    registry = build_current_source_registry(pi_source=PiSourceProvider.NOTPII)

    assert registry.get(KpiSource.PI_INTERPOLATED).definition.key.namespace == ('pi', 'not_pii')
    assert registry.get(KpiSource.PI_RECORDED).definition.key.namespace == ('pi', 'not_pii')


def test_pi_provider_must_be_explicit_and_typed() -> None:
    with pytest.raises(TypeError, match='pi_source must be PiSourceProvider'):
        build_current_source_registry(pi_source='notpii')  # type: ignore[arg-type]


def test_non_pi_bindings_are_independent_of_pi_provider() -> None:
    web_api = build_current_source_registry(pi_source=PiSourceProvider.PI_WEB_API)
    notpii = build_current_source_registry(pi_source=PiSourceProvider.NOTPII)

    for source in web_api.sources:
        if source in {KpiSource.PI_INTERPOLATED, KpiSource.PI_RECORDED}:
            continue
        assert web_api.get(source) == notpii.get(source)


def test_non_pi_sources_keep_source_and_partition_separate() -> None:
    registry = build_current_source_registry(pi_source=PiSourceProvider.PI_WEB_API)

    dispatch = registry.get(KpiSource.DISPATCH_STD_SHIFT_STATE)
    shift = dispatch.get_partition(KpiPartition.SHIFT)
    assert dispatch.definition.key.namespace == ('dispatch',)
    assert dispatch.definition.key.name == 'std_shift_state'
    assert shift.materialization == 'shift'
    assert shift.shift_column == 'shift_id'

    remanentes = registry.get(KpiSource.REMANENTES_STOCKS)
    assert remanentes.get_partition(KpiPartition.LATEST).materialization == 'latest'

    fabrica = registry.get(KpiSource.FABRICA_PLANES)
    assert fabrica.definition.key.namespace == ('fabrica',)
    assert fabrica.definition.key.name == 'planes'
    assert fabrica.get_partition(KpiPartition.DAILY).materialization == 'day'
    assert fabrica.get_partition(KpiPartition.WEEKLY).materialization == 'weekly'
    assert fabrica.definition.route_segments == ('fabrica', 'planes')
    assert fabrica.definition.get_materialization('day').resolved_route_segments == ('daily',)
    assert fabrica.definition.get_materialization('weekly').resolved_route_segments == ('weekly',)
