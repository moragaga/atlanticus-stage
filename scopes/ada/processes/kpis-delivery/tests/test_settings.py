import pytest

from ada.processes.kpis_delivery import (
    KpiDeliveryConfigurationError,
    KpiDeliveryProcessSettings,
    configuration_specs,
)
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.kernel import Environment


def _configuration(**values):
    base = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada-operaciones-integradas-local',
        'VOLUMEN_PATH': '/tmp/atlanticus',
        'COSMOS_CONSUMPTION_ENDPOINT': 'https://account.documents.azure.com',
        'COSMOS_CONSUMPTION_KEY': 'secret',
        'COSMOS_CONSUMPTION_DATABASE_NAME': 'ada',
        'COSMOS_CONSUMPTION_CONTAINER_NAME': 'application-data',
        'COSMOS_CONSUMPTION_ALLOW_INSECURE_HTTP': 'false',
        'KPI_DELIVERY_POLL_INTERVAL_SECONDS': '1',
        'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'off',
    }
    base.update(values)
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=base,
        sources={key: ConfigurationSource.PROCESS for key in base},
        sensitive_keys=frozenset({'COSMOS_CONSUMPTION_KEY'}),
    )


def test_settings_resolve_named_consumption_cosmos_connection() -> None:
    settings = KpiDeliveryProcessSettings.from_configuration(
        _configuration(KPI_DELIVERY_POLL_INTERVAL_SECONDS='3')
    )
    keys = {spec.key for spec in configuration_specs()}

    assert settings.cosmos.endpoint == 'https://account.documents.azure.com'
    assert settings.cosmos.database_name == 'ada'
    assert settings.container_name == 'application-data'
    assert settings.poll_interval_seconds == 3
    assert 'COSMOS_CONSUMPTION_ENDPOINT' in keys
    assert 'COSMOS_CONSUMPTION_KEY' in keys
    assert 'COSMOS_CONSUMPTION_DATABASE_NAME' in keys
    assert 'COSMOS_CONSUMPTION_CONTAINER_NAME' in keys
    cosmos_key_spec = next(
        spec for spec in configuration_specs() if spec.key == 'COSMOS_CONSUMPTION_KEY'
    )
    assert cosmos_key_spec.sensitive


def test_local_cosmos_http_requires_explicit_opt_in() -> None:
    settings = KpiDeliveryProcessSettings.from_configuration(
        _configuration(
            COSMOS_CONSUMPTION_ENDPOINT='http://localhost:8081',
            COSMOS_CONSUMPTION_ALLOW_INSECURE_HTTP='true',
        )
    )

    assert settings.cosmos.endpoint == 'http://localhost:8081'
    assert settings.cosmos.allow_insecure_http is True


def test_poll_interval_must_be_positive() -> None:
    with pytest.raises(KpiDeliveryConfigurationError, match='greater than zero'):
        KpiDeliveryProcessSettings.from_configuration(
            _configuration(KPI_DELIVERY_POLL_INTERVAL_SECONDS='0')
        )


def test_container_name_must_not_have_surrounding_whitespace() -> None:
    with pytest.raises(KpiDeliveryConfigurationError, match='surrounding whitespace'):
        KpiDeliveryProcessSettings.from_configuration(
            _configuration(COSMOS_CONSUMPTION_CONTAINER_NAME=' application-data ')
        )
