from __future__ import annotations

import pytest

from atlanticus.runtime import RuntimeConfiguration, RuntimeConfigurationError


def test_configuration_resolves_application_scope(tmp_path) -> None:
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path),
            'COSMOS_KEY_OPERATIONAL': 'secret',
        }
    )

    assert str(configuration.environment) == 'local'
    assert configuration.application == 'ada'
    assert configuration.application_root == tmp_path / 'ada'
    assert configuration.runtime_root == tmp_path / 'ada' / '.runtime'
    assert configuration.observability_file_logs_enabled is True
    assert not hasattr(configuration, 'values')
    assert not hasattr(configuration, 'get')
    assert not hasattr(configuration, 'require')


@pytest.mark.parametrize('environment', [None, '', 'LOCAL', ' local ', 'testing', 'stage'])
def test_configuration_preserves_strict_environment_contract(tmp_path, environment) -> None:
    values = {
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': str(tmp_path),
    }
    if environment is not None:
        values['ENVIRONMENT'] = environment

    with pytest.raises(RuntimeConfigurationError):
        RuntimeConfiguration.from_sources(environ=values)


def test_configuration_rejects_conflicting_cli_environment(tmp_path) -> None:
    with pytest.raises(RuntimeConfigurationError, match='conflicting environment'):
        RuntimeConfiguration.from_sources(
            cli_environment='dev',
            environ={
                'ENVIRONMENT': 'local',
                'APPLICATION': 'ada',
                'VOLUMEN_PATH': str(tmp_path),
            },
        )


def test_configuration_rejects_relative_volume_path() -> None:
    with pytest.raises(RuntimeConfigurationError, match='absolute'):
        RuntimeConfiguration.from_sources(
            environ={
                'ENVIRONMENT': 'local',
                'APPLICATION': 'ada',
                'VOLUMEN_PATH': '.local-volume',
            }
        )


def test_configuration_rejects_invalid_direct_contract(tmp_path) -> None:
    with pytest.raises(TypeError, match='Environment'):
        RuntimeConfiguration(environment='local', application='ada', volume_path=tmp_path)


@pytest.mark.parametrize('application', [' ada', 'ada ', 'ada/process', 'ada process'])
def test_configuration_rejects_ambiguous_application_identifiers(
    tmp_path,
    application,
) -> None:
    with pytest.raises((RuntimeConfigurationError, ValueError)):
        RuntimeConfiguration.from_sources(
            environ={
                'ENVIRONMENT': 'local',
                'APPLICATION': application,
                'VOLUMEN_PATH': str(tmp_path),
            }
        )


def test_configuration_resolves_observability_file_logs_flag(tmp_path) -> None:
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path),
            'ATLANTICUS_OBSERVABILITY_FILE_LOGS_ENABLED': 'false',
        }
    )

    assert configuration.observability_file_logs_enabled is False


@pytest.mark.parametrize('value', ['invalid', ' false ', 'TRUE '])
def test_configuration_rejects_invalid_observability_file_logs_flag(tmp_path, value) -> None:
    with pytest.raises(
        RuntimeConfigurationError, match='ATLANTICUS_OBSERVABILITY_FILE_LOGS_ENABLED'
    ):
        RuntimeConfiguration.from_sources(
            environ={
                'ENVIRONMENT': 'local',
                'APPLICATION': 'ada',
                'VOLUMEN_PATH': str(tmp_path),
                'ATLANTICUS_OBSERVABILITY_FILE_LOGS_ENABLED': value,
            }
        )
