from __future__ import annotations

import pytest

import atlanticus.configuration.bootstrap as bootstrap_module
from atlanticus.configuration import (
    ConfigurationBootstrap,
    ConfigurationSource,
    ConfigurationSourceError,
    ConfigurationValueError,
    ConfigurationVariableSpec,
    MissingConfigurationVariablesError,
)
from atlanticus.kernel import Environment, InvalidEnvironmentError


class FailingResolver:
    def get_secret(self, secret_name: str) -> str:
        raise AssertionError(f'Local bootstrap requested secret {secret_name}')


def test_local_uses_dotenv_and_process_values_without_mutating_process(tmp_path) -> None:
    dotenv_path = tmp_path / '.env'
    dotenv_path.write_text('TOKEN=from-dotenv\nTOPIC=events\n', encoding='utf-8')
    process_values = {'ENVIRONMENT': 'local', 'TOKEN': 'from-process'}
    bootstrap = ConfigurationBootstrap.from_process(
        specs=(
            ConfigurationVariableSpec(key='TOKEN', sensitive=True),
            ConfigurationVariableSpec(key='TOPIC'),
        ),
        process_values=process_values,
        dotenv_path=dotenv_path,
        secret_resolver=FailingResolver(),
    )

    configuration = bootstrap.load(process_values=process_values)

    assert configuration.require('TOKEN') == 'from-process'
    assert configuration.require('TOPIC') == 'events'
    assert configuration.sources['TOKEN'] == ConfigurationSource.PROCESS
    assert configuration.sources['TOPIC'] == ConfigurationSource.DOTENV
    assert process_values == {'ENVIRONMENT': 'local', 'TOKEN': 'from-process'}


def test_local_process_value_preserves_exact_whitespace() -> None:
    process_values = {'ENVIRONMENT': 'local', 'TOKEN': '  value with spaces  ', 'SPACE': ' '}
    bootstrap = ConfigurationBootstrap.from_process(
        specs=(
            ConfigurationVariableSpec(key='TOKEN'),
            ConfigurationVariableSpec(key='SPACE'),
        ),
        process_values=process_values,
    )

    configuration = bootstrap.load(process_values=process_values)

    assert configuration.require('TOKEN') == '  value with spaces  '
    assert configuration.require('SPACE') == ' '


def test_local_can_use_process_values_when_dotenv_does_not_exist(tmp_path) -> None:
    bootstrap = ConfigurationBootstrap.from_process(
        specs=(ConfigurationVariableSpec(key='TOPIC'),),
        process_values={'ENVIRONMENT': 'local', 'TOPIC': 'events'},
        dotenv_path=tmp_path / '.env',
    )

    configuration = bootstrap.load(process_values={'ENVIRONMENT': 'local', 'TOPIC': 'events'})

    assert configuration.to_dict() == {'ENVIRONMENT': 'local', 'TOPIC': 'events'}


def test_environment_is_required_before_loading_dotenv(tmp_path) -> None:
    (tmp_path / '.env').write_text('ENVIRONMENT=local\nTOPIC=events\n', encoding='utf-8')

    with pytest.raises(InvalidEnvironmentError):
        ConfigurationBootstrap.from_process(
            specs=(ConfigurationVariableSpec(key='TOPIC'),),
            process_values={},
            dotenv_path=tmp_path / '.env',
        )


def test_missing_local_variables_are_reported_together(tmp_path) -> None:
    bootstrap = ConfigurationBootstrap.from_process(
        specs=(
            ConfigurationVariableSpec(key='TOPIC'),
            ConfigurationVariableSpec(key='SUBSCRIPTION'),
        ),
        process_values={'ENVIRONMENT': 'local'},
        dotenv_path=tmp_path / '.env',
    )

    with pytest.raises(MissingConfigurationVariablesError) as captured:
        bootstrap.load(process_values={'ENVIRONMENT': 'local'})

    assert captured.value.variable_names == ('SUBSCRIPTION', 'TOPIC')


def test_empty_process_value_does_not_fall_back_to_dotenv(tmp_path) -> None:
    dotenv_path = tmp_path / '.env'
    dotenv_path.write_text('TOKEN=from-dotenv\n', encoding='utf-8')
    bootstrap = ConfigurationBootstrap.from_process(
        specs=(ConfigurationVariableSpec(key='TOKEN', sensitive=True),),
        process_values={'ENVIRONMENT': 'local', 'TOKEN': ''},
        dotenv_path=dotenv_path,
    )

    with pytest.raises(MissingConfigurationVariablesError, match='TOKEN'):
        bootstrap.load(process_values={'ENVIRONMENT': 'local', 'TOKEN': ''})


@pytest.mark.parametrize(
    'process_values',
    [
        {},
        {'ENVIRONMENT': 'dev'},
    ],
)
def test_load_rejects_missing_or_changed_environment(process_values: dict[str, str]) -> None:
    bootstrap = ConfigurationBootstrap(
        environment=Environment.from_value('local'),
        specs=(),
    )

    with pytest.raises(ConfigurationSourceError, match='ENVIRONMENT'):
        bootstrap.load(process_values=process_values)


def test_bootstrap_rejects_invalid_public_inputs() -> None:
    with pytest.raises(ConfigurationSourceError):
        ConfigurationBootstrap(environment='local', specs=())  # type: ignore[arg-type]
    with pytest.raises(ConfigurationSourceError):
        ConfigurationBootstrap(
            environment=Environment.from_value('local'),
            specs=('TOPIC',),  # type: ignore[arg-type]
        )
    with pytest.raises(ConfigurationSourceError, match='ENVIRONMENT'):
        ConfigurationBootstrap(
            environment=Environment.from_value('local'),
            specs=(ConfigurationVariableSpec(key='ENVIRONMENT'),),
        )
    with pytest.raises(ConfigurationSourceError):
        ConfigurationBootstrap.from_process(
            specs=None,  # type: ignore[arg-type]
            process_values={'ENVIRONMENT': 'local'},
        )
    with pytest.raises(ConfigurationSourceError):
        ConfigurationBootstrap.from_process(
            specs=(),
            process_values={'ENVIRONMENT': 'local'},
            dotenv_path=123,  # type: ignore[arg-type]
        )


def test_invalid_process_value_is_a_controlled_configuration_error() -> None:
    bootstrap = ConfigurationBootstrap(
        environment=Environment.from_value('local'),
        specs=(ConfigurationVariableSpec(key='TOPIC'),),
    )

    with pytest.raises(ConfigurationValueError, match='strings'):
        bootstrap.load(process_values={'ENVIRONMENT': 'local', 'TOPIC': 123})  # type: ignore[dict-item]


def test_dotenv_read_failure_is_classified_as_source_error(tmp_path, monkeypatch) -> None:
    dotenv_path = tmp_path / '.env'
    dotenv_path.write_text('TOPIC=events\n', encoding='utf-8')
    bootstrap = ConfigurationBootstrap(
        environment=Environment.from_value('local'),
        specs=(ConfigurationVariableSpec(key='TOPIC'),),
        dotenv_path=dotenv_path,
    )

    def fail_read(_path):
        raise OSError('sensitive physical detail')

    monkeypatch.setattr(bootstrap_module, 'dotenv_values', fail_read)

    with pytest.raises(ConfigurationSourceError) as captured:
        bootstrap.load(process_values={'ENVIRONMENT': 'local'})

    assert str(dotenv_path) in str(captured.value)
    assert 'sensitive physical detail' not in str(captured.value)
