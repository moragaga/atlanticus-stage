from ada.processes.fabrica.composition import FABRICA_JOB_DEFINITION
from ada.processes.fabrica.settings import FabricaSettings, configuration_specs
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.connectivity.storage import StorageSasCredential
from atlanticus.kernel import Environment


def _configuration(**overrides: str) -> ResolvedConfiguration:
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': '/tmp/ada',
        'STORAGE_ACCOUNT_SAS_URL_FABRICA_PLANES': 'https://plan.blob.core.windows.net/planes',
        'STORAGE_ACCOUNT_SAS_TOKEN_FABRICA_PLANES': 'sv=plan',
        'STORAGE_ACCOUNT_SAS_URL_FABRICA_KPIS': 'https://kpi.blob.core.windows.net/kpis?sv=kpi',
        'FABRICA_IDLE_SECONDS': '5',
        **overrides,
    }
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
    )


def test_runtime_matches_current_process_standard() -> None:
    definition = FABRICA_JOB_DEFINITION
    assert definition.iteration_timeout_seconds == 240
    assert definition.execution_timeout_seconds == 600
    assert definition.shutdown_grace_seconds == 10
    assert definition.lease_timeout_seconds == 30
    assert definition.lease_renew_seconds == 10
    assert definition.lease_wait_seconds is None
    assert definition.lease_poll_seconds == 1


def test_settings_build_two_independent_named_sas_connections() -> None:
    settings = FabricaSettings.from_configuration(_configuration())
    assert set(settings.connections) == {'planes', 'kpis'}
    planes = settings.connections['planes']
    kpis = settings.connections['kpis']
    assert planes.container_name == 'planes'
    assert kpis.container_name == 'kpis'
    assert isinstance(planes.settings.credential, StorageSasCredential)
    assert isinstance(kpis.settings.credential, StorageSasCredential)
    assert planes.settings.credential.account_url == 'https://plan.blob.core.windows.net'
    assert kpis.settings.credential.account_url == 'https://kpi.blob.core.windows.net'
    assert planes.settings.credential.sas_token == 'sv=plan'
    assert kpis.settings.credential.sas_token == 'sv=kpi'


def test_configuration_specs_keep_both_legacy_sas_names() -> None:
    keys = {spec.key for spec in configuration_specs()}
    assert 'STORAGE_ACCOUNT_SAS_URL_FABRICA_PLANES' in keys
    assert 'STORAGE_ACCOUNT_SAS_TOKEN_FABRICA_PLANES' in keys
    assert 'STORAGE_ACCOUNT_SAS_URL_FABRICA_KPIS' in keys
    assert 'STORAGE_ACCOUNT_SAS_TOKEN_FABRICA_KPIS' in keys
