import pytest

from ada.kpis.core import KpiSource
from ada.kpis.sources import (
    PiSourceProvider,
    TimePartitionGranularity,
    build_current_source_registry,
)


def test_current_registry_covers_every_typed_kpi_source() -> None:
    registry = build_current_source_registry(pi_source=PiSourceProvider.PI_WEB_API)

    assert set(registry.sources) == set(KpiSource)


def test_pi_web_api_provider_binds_generic_pi_sources() -> None:
    registry = build_current_source_registry(pi_source=PiSourceProvider.PI_WEB_API)

    interpolated = registry.get(KpiSource.PI_INTERPOLATED)
    assert interpolated.definition.key.namespace == ('pi', 'web-api')
    assert interpolated.definition.key.name == 'interpolated'
    assert interpolated.snapshot_materialization == 'latest'
    assert interpolated.time_materialization == 'daily'
    assert interpolated.time_partition_granularity is TimePartitionGranularity.DAY
    assert interpolated.timestamp_column == 'timestamp_utc'

    recorded = registry.get(KpiSource.PI_RECORDED)
    assert recorded.definition.key.namespace == ('pi', 'web-api')
    assert recorded.definition.key.name == 'recorded'
    assert recorded.time_materialization == 'monthly'
    assert recorded.time_partition_granularity is TimePartitionGranularity.MONTH


def test_notpii_provider_binds_same_generic_pi_sources() -> None:
    registry = build_current_source_registry(pi_source=PiSourceProvider.NOTPII)

    interpolated = registry.get(KpiSource.PI_INTERPOLATED)
    recorded = registry.get(KpiSource.PI_RECORDED)

    assert interpolated.definition.key.namespace == ('pi', 'not_pii')
    assert interpolated.definition.key.name == 'interpolated'
    assert recorded.definition.key.namespace == ('pi', 'not_pii')
    assert recorded.definition.key.name == 'recorded'


def test_pi_provider_must_be_explicit_and_typed() -> None:
    with pytest.raises(TypeError, match='pi_source must be PiSourceProvider'):
        build_current_source_registry(pi_source='notpii')  # type: ignore[arg-type]


def test_non_pi_bindings_are_independent_of_pi_provider() -> None:
    web_api = build_current_source_registry(pi_source=PiSourceProvider.PI_WEB_API)
    notpii = build_current_source_registry(pi_source=PiSourceProvider.NOTPII)

    for source in KpiSource:
        if source in {KpiSource.PI_INTERPOLATED, KpiSource.PI_RECORDED}:
            continue
        assert web_api.get(source) == notpii.get(source)


def test_current_registry_matches_current_non_pi_dataset_contracts() -> None:
    registry = build_current_source_registry(pi_source=PiSourceProvider.PI_WEB_API)

    dispatch = registry.get(KpiSource.DISPATCH_STD_SHIFT_STATE)
    assert dispatch.definition.key.namespace == ('dispatch',)
    assert dispatch.definition.key.name == 'std_shift_state'
    assert dispatch.shift_materialization == 'shift'
    assert dispatch.shift_column == 'shift_id'

    blockgrade = registry.get(KpiSource.BLOCKGRADE_MMS_BLOCKGRADE_DETAILS_BUCKET)
    assert blockgrade.definition.key.namespace == ('blockgrade',)
    assert blockgrade.definition.key.name == 'mms_blockgrade_details_bucket'

    remanentes = registry.get(KpiSource.REMANENTES_STOCKS)
    assert remanentes.definition.key.namespace == ('remanentes',)
    assert remanentes.definition.key.name == 'stocks'
    assert remanentes.snapshot_materialization == 'latest'
    assert remanentes.timestamp_column == 'timestamp'

    fabrica = registry.get(KpiSource.FABRICA_PLANES_DAILY)
    assert fabrica.definition.key.namespace == ('fabrica',)
    assert fabrica.definition.key.name == 'planes'
    assert fabrica.snapshot_materialization == 'daily'
    assert fabrica.timestamp_column == 'timestamp'
