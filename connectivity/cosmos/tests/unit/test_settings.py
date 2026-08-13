from __future__ import annotations

import pytest

from atlanticus.connectivity.cosmos import (
    CosmosConfigurationError,
    CosmosSettings,
    sanitize_endpoint,
)


def test_settings_normalize_endpoint_hide_key_and_require_https_by_default() -> None:
    settings = CosmosSettings(
        endpoint=' HTTPS://account.documents.azure.com/ ',
        key='private-key',
        database_name='atlanticus',
    )

    assert settings.endpoint == 'https://account.documents.azure.com'
    assert 'private-key' not in repr(settings)

    with pytest.raises(CosmosConfigurationError):
        CosmosSettings(
            endpoint='http://localhost:8081',
            key='key',
            database_name='atlanticus',
        )


def test_http_requires_explicit_emulator_opt_in() -> None:
    settings = CosmosSettings(
        endpoint='http://localhost:8081',
        key='key',
        database_name='atlanticus',
        allow_insecure_http=True,
    )

    assert settings.allow_insecure_http is True


def test_key_is_preserved_exactly() -> None:
    settings = CosmosSettings(
        endpoint='https://account.documents.azure.com',
        key='  private-key  ',
        database_name='atlanticus',
    )

    assert settings.key == '  private-key  '


def test_identifiers_and_scalar_settings_are_strict() -> None:
    base = {
        'endpoint': 'https://account.documents.azure.com',
        'key': 'key',
        'database_name': 'atlanticus',
    }

    with pytest.raises(CosmosConfigurationError):
        CosmosSettings(**base, page_size=True)
    with pytest.raises(CosmosConfigurationError):
        CosmosSettings(**base, allow_insecure_http=1)
    with pytest.raises(CosmosConfigurationError):
        CosmosSettings(**{**base, 'database_name': ' atlanticus '})
    with pytest.raises(CosmosConfigurationError):
        CosmosSettings(**{**base, 'database_name': 'atlanticus/db'})
    with pytest.raises(CosmosConfigurationError):
        CosmosSettings(**{**base, 'key': ''})


def test_endpoint_rejects_credentials_paths_query_and_fragments() -> None:
    base = {'key': 'key', 'database_name': 'atlanticus'}
    invalid = (
        'https://user:pass@account.documents.azure.com',
        'https://account.documents.azure.com/path',
        'https://account.documents.azure.com?x=1',
        'https://account.documents.azure.com#fragment',
    )

    for endpoint in invalid:
        with pytest.raises(CosmosConfigurationError):
            CosmosSettings(endpoint=endpoint, **base)


def test_sanitize_endpoint_never_returns_path_or_credentials() -> None:
    assert (
        sanitize_endpoint('https://account.documents.azure.com/path?secret=value')
        == 'https://account.documents.azure.com'
    )
    assert (
        sanitize_endpoint('https://user:private@account.documents.azure.com:443/path')
        == 'https://account.documents.azure.com:443'
    )
    assert sanitize_endpoint(object()) == '<invalid>'
