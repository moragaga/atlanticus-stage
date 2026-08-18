from __future__ import annotations

from ada.processes.remanentes.composition import REMANENTES_JOB_DEFINITION
from ada.processes.remanentes.settings import RemanentesSettings, configuration_specs
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.connectivity.storage import StorageConnectionStringCredential
from atlanticus.kernel import Environment


def _configuration(**overrides: str) -> ResolvedConfiguration:
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': '/tmp/ada',
        'STORAGE_ACCOUNT_CONNECTION_STRING_REMANENTES': 'UseDevelopmentStorage=true',
        'STORAGE_ACCOUNT_CONTAINER_NAME_REMANENTES': 'dataproduct',
        'REMANENTES_SOURCE_TIMEZONE': 'America/Santiago',
        'REMANENTES_IDLE_SECONDS': '30',
        **overrides,
    }
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
    )


def test_runtime_matches_current_process_standard() -> None:
    definition = REMANENTES_JOB_DEFINITION
    assert (
        definition.iteration_timeout_seconds,
        definition.execution_timeout_seconds,
        definition.shutdown_grace_seconds,
    ) == (240, 600, 10)
    assert (
        definition.lease_timeout_seconds,
        definition.lease_renew_seconds,
        definition.lease_wait_seconds,
        definition.lease_poll_seconds,
    ) == (30, 10, None, 1)


def test_settings_preserve_connection_string_container_and_timezone() -> None:
    settings = RemanentesSettings.from_configuration(_configuration())

    assert settings.connection.container_name == 'dataproduct'
    assert isinstance(settings.connection.settings.credential, StorageConnectionStringCredential)
    assert settings.source_timezone_name == 'America/Santiago'
    assert settings.idle_seconds == 30


def test_configuration_specs_preserve_legacy_names() -> None:
    keys = {spec.key for spec in configuration_specs()}

    assert 'STORAGE_ACCOUNT_CONNECTION_STRING_REMANENTES' in keys
    assert 'STORAGE_ACCOUNT_CONTAINER_NAME_REMANENTES' in keys
    assert 'REMANENTES_SOURCE_TIMEZONE' in keys
