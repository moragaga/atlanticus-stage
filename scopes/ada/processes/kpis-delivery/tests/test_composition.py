from ada.processes.kpis_delivery import KpiDeliveryBindingsRepository, build_composition
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.kernel import Environment


def _configuration(tmp_path):
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada-operaciones-integradas-local',
        'VOLUMEN_PATH': str(tmp_path),
        'COSMOS_CONSUMPTION_ENDPOINT': 'https://account.documents.azure.com',
        'COSMOS_CONSUMPTION_KEY': 'secret',
        'COSMOS_CONSUMPTION_DATABASE_NAME': 'ada',
        'COSMOS_CONSUMPTION_CONTAINER_NAME': 'application-data',
        'COSMOS_CONSUMPTION_ALLOW_INSECURE_HTTP': 'false',
        'KPI_DELIVERY_POLL_INTERVAL_SECONDS': '3',
        'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'off',
    }
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
        sensitive_keys=frozenset({'COSMOS_CONSUMPTION_KEY'}),
    )


def test_composition_uses_shared_application_and_named_consumption_connection(tmp_path) -> None:
    composition = build_composition(configuration=_configuration(tmp_path))

    assert composition.runtime_configuration.application == 'ada-operaciones-integradas-local'
    assert composition.runtime_configuration.application_root == (
        tmp_path / 'ada-operaciones-integradas-local'
    )
    assert composition.settings.container_name == 'application-data'
    assert composition.cosmos_client.settings.database_name == 'ada'
    assert composition.job_definition.service_name == 'kpis-delivery'
    assert composition.job_definition.job_key == 'kpis-delivery'
    assert composition.job_definition.sleep_seconds == 3
    assert isinstance(composition.job._bindings, KpiDeliveryBindingsRepository)
    assert composition.job._bindings.client is composition.cosmos_client
    assert composition.job._snapshots.client is composition.cosmos_client
