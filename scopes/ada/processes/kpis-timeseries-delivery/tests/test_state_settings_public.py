from datetime import UTC, datetime

import pytest

from ada.kpis.core import KpiWatermark
from ada.processes.kpis_timeseries_delivery import (
    KPI_TIMESERIES_DELIVERY_CONTAINER_NAME,
    KpiHistorianWatermarkStore,
    KpiTimeseriesCheckpoint,
    KpiTimeseriesDeliveryCheckpointStore,
    KpiTimeseriesDeliveryProcessSettings,
    KpiTimeseriesDeliveryRepositoryError,
    configuration_specs,
)
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.kernel import Environment
from atlanticus.state import AtomicStateStore, StateKey


def test_historian_watermark_reader_uses_existing_historian_state(tmp_path) -> None:
    store = AtomicStateStore(volume_path=tmp_path, application='ada')
    watermark = KpiWatermark(datetime(2026, 8, 25, 10, 0, tzinfo=UTC))
    store.replace(
        StateKey(namespace=('kpis-historian',), name='committed-watermark'),
        watermark.as_document(),
    )

    assert KpiHistorianWatermarkStore(store=store).read_watermark() == watermark


def test_checkpoint_roundtrip_and_rejects_watermark_reset(tmp_path) -> None:
    store = KpiTimeseriesDeliveryCheckpointStore(
        store=AtomicStateStore(volume_path=tmp_path, application='ada')
    )
    checkpoint = KpiTimeseriesCheckpoint(
        KpiWatermark(datetime(2026, 8, 25, 10, 0, tzinfo=UTC)),
        'config-1',
    )
    store.commit(checkpoint)

    assert store.read() == checkpoint
    with pytest.raises(KpiTimeseriesDeliveryRepositoryError, match='must not regress'):
        store.commit(KpiTimeseriesCheckpoint(None, 'config-2'))


def test_settings_keep_container_names_out_of_environment(tmp_path) -> None:
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': str(tmp_path),
        'COSMOS_CONSUMPTION_ENDPOINT': 'http://localhost:8081',
        'COSMOS_CONSUMPTION_KEY': 'key',
        'COSMOS_CONSUMPTION_DATABASE_NAME': 'ada',
        'KPI_TIMESERIES_DELIVERY_POLL_INTERVAL_SECONDS': '10',
        'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'off',
    }
    configuration = ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
        sensitive_keys=frozenset({'COSMOS_CONSUMPTION_KEY'}),
    )

    settings = KpiTimeseriesDeliveryProcessSettings.from_configuration(configuration)
    keys = {spec.key for spec in configuration_specs()}

    assert settings.cosmos.allow_insecure_http
    assert 'COSMOS_CONSUMPTION_CONTAINER_NAME' not in keys
    assert KPI_TIMESERIES_DELIVERY_CONTAINER_NAME == 'kpis-timeseries-delivery'
