from ada.processes.kpis_timeseries_delivery import (
    KpiHistorianWatermarkStore,
    KpiTimeseriesConfigurationRepository,
    KpiTimeseriesDeliveryCheckpointStore,
    KpiTimeseriesHistoryRepository,
    KpiTimeseriesSnapshotRepository,
    build_composition,
)
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.kernel import Environment


def _configuration(tmp_path):
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada-operaciones-integradas-local',
        'VOLUMEN_PATH': str(tmp_path),
        'COSMOS_CONSUMPTION_ENDPOINT': 'http://localhost:8081',
        'COSMOS_CONSUMPTION_KEY': 'secret',
        'COSMOS_CONSUMPTION_DATABASE_NAME': 'ada',
        'KPI_TIMESERIES_DELIVERY_POLL_INTERVAL_SECONDS': '7',
        'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'off',
    }
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
        sensitive_keys=frozenset({'COSMOS_CONSUMPTION_KEY'}),
    )


def test_composition_reuses_shared_runtime_boundaries(tmp_path) -> None:
    composition = build_composition(configuration=_configuration(tmp_path))

    assert composition.runtime_configuration.application == 'ada-operaciones-integradas-local'
    assert composition.cosmos_client.settings.database_name == 'ada'
    assert composition.job_definition.service_name == 'kpis-timeseries-delivery'
    assert composition.job_definition.job_key == 'kpis-timeseries-delivery'
    assert composition.job_definition.sleep_seconds == 7
    assert isinstance(composition.job._configuration_reader, KpiTimeseriesConfigurationRepository)
    assert isinstance(composition.job._historian_state, KpiHistorianWatermarkStore)
    assert isinstance(composition.job._history, KpiTimeseriesHistoryRepository)
    assert isinstance(composition.job._checkpoint_store, KpiTimeseriesDeliveryCheckpointStore)
    assert isinstance(composition.job._snapshots, KpiTimeseriesSnapshotRepository)
    assert composition.job._configuration_reader.client is composition.cosmos_client
    assert composition.job._snapshots.client is composition.cosmos_client
