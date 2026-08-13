from __future__ import annotations

import pytest

from atlanticus.connectivity.storage import (
    StorageConfigurationError,
    StorageConnectionStringCredential,
    StorageSasCredential,
    StorageSettings,
    sanitize_account_url,
)


def test_connection_string_is_preserved_exactly_and_hidden_from_repr() -> None:
    value = '  DefaultEndpointsProtocol=https;AccountName=a;AccountKey=secret==;  '
    credential = StorageConnectionStringCredential(connection_string=value)
    assert credential.connection_string == value
    assert 'secret' not in repr(credential)


def test_sas_secret_is_preserved_and_account_url_is_normalized() -> None:
    credential = StorageSasCredential(
        account_url=' HTTPS://Example.blob.core.windows.net/ ',
        sas_token='?sv=1&sig=secret',
    )
    assert credential.account_url == 'https://Example.blob.core.windows.net'
    assert credential.sas_token == '?sv=1&sig=secret'
    assert 'secret' not in repr(credential)


def test_sas_http_requires_explicit_local_opt_in() -> None:
    with pytest.raises(StorageConfigurationError, match='allow_insecure_http'):
        StorageSasCredential(account_url='http://azurite:10000/devstoreaccount1', sas_token='sv=1')

    credential = StorageSasCredential(
        account_url='http://azurite:10000/devstoreaccount1/',
        sas_token='sv=1',
        allow_insecure_http=True,
    )
    assert credential.account_url == 'http://azurite:10000/devstoreaccount1'


@pytest.mark.parametrize(
    'account_url',
    [
        'storage.example/path',
        'https://user:password@storage.example',
        'https://storage.example/?sig=secret',
        'ftp://storage.example',
    ],
)
def test_sas_rejects_unsafe_account_urls(account_url: str) -> None:
    with pytest.raises(StorageConfigurationError):
        StorageSasCredential(account_url=account_url, sas_token='sv=1')


@pytest.mark.parametrize('secret', ['', 'bad\nsecret', 'bad\rsecret', 'bad\x00secret'])
def test_credentials_reject_invalid_secret_values(secret: str) -> None:
    with pytest.raises(StorageConfigurationError):
        StorageConnectionStringCredential(connection_string=secret)
    with pytest.raises(StorageConfigurationError):
        StorageSasCredential(account_url='https://storage.example', sas_token=secret)


def test_settings_accept_both_credential_types() -> None:
    connection = StorageSettings(
        credential=StorageConnectionStringCredential(connection_string='UseDevelopmentStorage=true')
    )
    sas = StorageSettings(
        credential=StorageSasCredential(account_url='https://storage.example', sas_token='sv=1')
    )
    assert isinstance(connection.credential, StorageConnectionStringCredential)
    assert isinstance(sas.credential, StorageSasCredential)


@pytest.mark.parametrize(
    'field,value',
    [
        ('connection_timeout_seconds', 0),
        ('connection_timeout_seconds', True),
        ('read_timeout_seconds', -1),
        ('max_list_items', 0),
    ],
)
def test_settings_reject_invalid_limits(field: str, value: object) -> None:
    kwargs = {
        'credential': StorageConnectionStringCredential(
            connection_string='UseDevelopmentStorage=true'
        ),
        field: value,
    }
    with pytest.raises(StorageConfigurationError):
        StorageSettings(**kwargs)


def test_settings_reject_unknown_credential_type() -> None:
    with pytest.raises(StorageConfigurationError, match='credential'):
        StorageSettings(credential='secret')  # type: ignore[arg-type]


def test_sanitize_account_url_removes_query_fragment_and_keeps_emulator_path() -> None:
    assert (
        sanitize_account_url('http://azurite:10000/devstoreaccount1/?sig=secret#fragment')
        == 'http://azurite:10000/devstoreaccount1'
    )
    assert sanitize_account_url('not-a-url') == '<invalid>'
