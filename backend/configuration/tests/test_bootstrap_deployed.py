from __future__ import annotations

import json

import pytest

from atlanticus.configuration import (
    ConfigurationBootstrap,
    ConfigurationSource,
    ConfigurationSourceError,
    ConfigurationVariableSpec,
    SecretResolutionError,
    SecretsManifest,
)
from atlanticus.kernel import Environment


class FakeResolver:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.requests: list[str] = []

    def get_secret(self, secret_name: str) -> str:
        self.requests.append(secret_name)
        return self.values[secret_name]


def _manifest(tmp_path) -> SecretsManifest:
    path = tmp_path / 'secrets.json'
    path.write_text(
        json.dumps(
            [
                {
                    'var_name': 'TOPIC',
                    'secret_name': None,
                    'value': 'events',
                    'exists_in_key_vault': False,
                },
                {
                    'var_name': 'CONNECTION_STRING',
                    'secret_name': 'secret-service-bus',
                    'value': None,
                    'exists_in_key_vault': True,
                },
                {
                    'var_name': 'UNUSED_SECRET',
                    'secret_name': 'secret-unused',
                    'value': None,
                    'exists_in_key_vault': True,
                },
            ]
        ),
        encoding='utf-8',
    )
    return SecretsManifest.from_path(path)


def test_deployed_environment_uses_manifest_and_key_vault_only(tmp_path) -> None:
    dotenv_path = tmp_path / '.env'
    dotenv_path.write_text(
        'TOPIC=dotenv-topic\nCONNECTION_STRING=dotenv-secret\n',
        encoding='utf-8',
    )
    resolver = FakeResolver({'secret-service-bus': 'resolved-secret'})
    bootstrap = ConfigurationBootstrap(
        environment=Environment.from_value('dev'),
        specs=(
            ConfigurationVariableSpec(key='TOPIC'),
            ConfigurationVariableSpec(key='CONNECTION_STRING', sensitive=True),
        ),
        dotenv_path=dotenv_path,
        secrets_manifest=_manifest(tmp_path),
        secret_resolver=resolver,
    )

    configuration = bootstrap.load(
        process_values={
            'ENVIRONMENT': 'dev',
            'TOPIC': 'process-topic',
            'CONNECTION_STRING': 'process-secret',
        }
    )

    assert configuration.require('TOPIC') == 'events'
    assert configuration.require('CONNECTION_STRING') == 'resolved-secret'
    assert configuration.sources['TOPIC'] == ConfigurationSource.MANIFEST
    assert configuration.sources['CONNECTION_STRING'] == ConfigurationSource.KEY_VAULT
    assert resolver.requests == ['secret-service-bus']
    assert 'resolved-secret' not in repr(configuration)


def test_deployed_environment_requires_manifest() -> None:
    bootstrap = ConfigurationBootstrap(
        environment=Environment.from_value('prd'),
        specs=(ConfigurationVariableSpec(key='TOPIC'),),
    )

    with pytest.raises(ConfigurationSourceError, match='prd'):
        bootstrap.load(process_values={'ENVIRONMENT': 'prd', 'TOPIC': 'ignored'})


def test_key_vault_failure_reports_variable_without_sensitive_details(tmp_path) -> None:
    class LeakingResolver:
        def get_secret(self, secret_name: str) -> str:
            raise RuntimeError('credential-and-secret-value')

    bootstrap = ConfigurationBootstrap(
        environment=Environment.from_value('uat'),
        specs=(ConfigurationVariableSpec(key='CONNECTION_STRING', sensitive=True),),
        secrets_manifest=_manifest(tmp_path),
        secret_resolver=LeakingResolver(),
    )

    with pytest.raises(SecretResolutionError) as captured:
        bootstrap.load(process_values={'ENVIRONMENT': 'uat'})

    assert captured.value.variable_name == 'CONNECTION_STRING'
    assert 'CONNECTION_STRING' in str(captured.value)
    assert 'credential-and-secret-value' not in str(captured.value)


def test_deployed_bootstrap_rejects_environment_change_before_resolving_secret(tmp_path) -> None:
    resolver = FakeResolver({'secret-service-bus': 'resolved-secret'})
    bootstrap = ConfigurationBootstrap(
        environment=Environment.from_value('dev'),
        specs=(ConfigurationVariableSpec(key='CONNECTION_STRING', sensitive=True),),
        secrets_manifest=_manifest(tmp_path),
        secret_resolver=resolver,
    )

    with pytest.raises(ConfigurationSourceError, match='ENVIRONMENT'):
        bootstrap.load(process_values={'ENVIRONMENT': 'prd'})

    assert resolver.requests == []


def test_secret_resolver_must_return_a_non_empty_string(tmp_path) -> None:
    class InvalidResolver:
        def get_secret(self, secret_name: str) -> int:
            return 123

    bootstrap = ConfigurationBootstrap(
        environment=Environment.from_value('uat'),
        specs=(ConfigurationVariableSpec(key='CONNECTION_STRING', sensitive=True),),
        secrets_manifest=_manifest(tmp_path),
        secret_resolver=InvalidResolver(),  # type: ignore[arg-type]
    )

    with pytest.raises(SecretResolutionError, match='CONNECTION_STRING'):
        bootstrap.load(process_values={'ENVIRONMENT': 'uat'})


def test_deployed_source_discriminator_ignores_the_inactive_field_and_preserves_values(tmp_path) -> None:
    path = tmp_path / 'secrets.json'
    path.write_text(
        json.dumps(
            [
                {
                    'var_name': 'STATIC_VALUE',
                    'secret_name': 'ignored-static-secret',
                    'value': '  static value  ',
                    'exists_in_key_vault': False,
                },
                {
                    'var_name': 'SECRET_VALUE',
                    'secret_name': 'secret-value',
                    'value': 'ignored-fallback',
                    'exists_in_key_vault': True,
                },
            ]
        ),
        encoding='utf-8',
    )
    resolver = FakeResolver({'secret-value': '  resolved secret  '})
    bootstrap = ConfigurationBootstrap(
        environment=Environment.from_value('dev'),
        specs=(
            ConfigurationVariableSpec(key='STATIC_VALUE'),
            ConfigurationVariableSpec(key='SECRET_VALUE', sensitive=True),
        ),
        secrets_manifest=SecretsManifest.from_path(path),
        secret_resolver=resolver,
    )

    configuration = bootstrap.load(process_values={'ENVIRONMENT': 'dev'})

    assert configuration.require('STATIC_VALUE') == '  static value  '
    assert configuration.require('SECRET_VALUE') == '  resolved secret  '
    assert configuration.sources['STATIC_VALUE'] == ConfigurationSource.MANIFEST
    assert configuration.sources['SECRET_VALUE'] == ConfigurationSource.KEY_VAULT
    assert resolver.requests == ['secret-value']


def test_deployed_preserves_whitespace_only_values(tmp_path) -> None:
    path = tmp_path / 'secrets.json'
    path.write_text(
        json.dumps(
            [
                {
                    'var_name': 'STATIC_SPACE',
                    'secret_name': None,
                    'value': ' ',
                    'exists_in_key_vault': False,
                },
                {
                    'var_name': 'SECRET_SPACE',
                    'secret_name': 'secret-space',
                    'value': None,
                    'exists_in_key_vault': True,
                },
            ]
        ),
        encoding='utf-8',
    )
    resolver = FakeResolver({'secret-space': ' '})
    bootstrap = ConfigurationBootstrap(
        environment=Environment.from_value('uat'),
        specs=(
            ConfigurationVariableSpec(key='STATIC_SPACE'),
            ConfigurationVariableSpec(key='SECRET_SPACE', sensitive=True),
        ),
        secrets_manifest=SecretsManifest.from_path(path),
        secret_resolver=resolver,
    )

    configuration = bootstrap.load(process_values={'ENVIRONMENT': 'uat'})

    assert configuration.require('STATIC_SPACE') == ' '
    assert configuration.require('SECRET_SPACE') == ' '

