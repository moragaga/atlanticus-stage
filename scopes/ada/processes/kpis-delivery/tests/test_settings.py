from ada.processes.kpis_delivery import KpiDeliveryProcessSettings, configuration_specs
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.kernel import Environment


def resolved(tmp_path):
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': str(tmp_path),
        'COSMOS_CONSUMPTION_ENDPOINT': 'http://localhost:8081',
        'COSMOS_CONSUMPTION_KEY': 'key',
        'COSMOS_CONSUMPTION_DATABASE_NAME': 'ada',
        'KPI_DELIVERY_POLL_INTERVAL_SECONDS': '10',
        'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'off',
    }
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
        sensitive_keys=frozenset({'COSMOS_CONSUMPTION_KEY'}),
    )


def test_settings_only_require_cosmos_endpoint_key_and_database(tmp_path):
    settings = KpiDeliveryProcessSettings.from_configuration(resolved(tmp_path))

    assert settings.cosmos.database_name == 'ada'
    assert settings.cosmos.allow_insecure_http is True
    keys = {spec.key for spec in configuration_specs()}
    assert 'COSMOS_CONSUMPTION_CONTAINER_NAME' not in keys
    assert 'COSMOS_CONSUMPTION_ALLOW_INSECURE_HTTP' not in keys
