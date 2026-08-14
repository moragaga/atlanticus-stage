from __future__ import annotations

import os
import time

import pytest
from azure.core.credentials import AccessToken, TokenCredential
from azure.core.pipeline.transport import HttpRequest, HttpResponse, RequestsTransport
from azure.keyvault.secrets import SecretClient as AzureSecretClient

import atlanticus.connectivity.key_vault.client as key_vault_client_module
from atlanticus.connectivity.key_vault import KeyVaultClient, KeyVaultSettings
from atlanticus.connectivity.storage import (
    StorageClient,
    StorageConnectionStringCredential,
    StorageSettings,
)
from atlanticus.kernel import Environment

pytestmark = pytest.mark.integration
_RUN = os.getenv('ATLANTICUS_RUN_STORAGE_AZURE_LOCAL_INTEGRATION') == '1'
_PAYLOAD = b'atlanticus-azure-local-storage'


class _StaticTokenCredential(TokenCredential):
    def get_token(self, *scopes: str, **kwargs: object) -> AccessToken:
        del scopes, kwargs
        return AccessToken('atlanticus-azure-local-token', int(time.time()) + 3600)

    def close(self) -> None:
        return


class _ForceHttpTransport(RequestsTransport):
    def send(self, request: HttpRequest, **kwargs: object) -> HttpResponse:
        if request.url.startswith('https://'):
            request.url = f'http://{request.url.removeprefix("https://")}'
        return super().send(request, **kwargs)


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f'{name} is required for the Azure-local integration.')
    return value


def _floci_vault_url() -> str:
    endpoint = _required_environment('ATLANTICUS_FLOCI_AZ_ENDPOINT').rstrip('/')
    account_name = _required_environment('ATLANTICUS_FLOCI_AZ_ACCOUNT_NAME')
    if not endpoint.startswith('http://'):
        pytest.fail('ATLANTICUS_FLOCI_AZ_ENDPOINT must use local http:// transport.')
    return f'https://{endpoint.removeprefix("http://")}/{account_name}-keyvault'


def _key_vault_settings() -> KeyVaultSettings:
    return KeyVaultSettings(
        company_abrev='ATL',
        environment=Environment.from_value('local'),
        product_abrev='TEST',
    )


def _install_floci_key_vault_sdk(
    monkeypatch: pytest.MonkeyPatch,
    settings: KeyVaultSettings,
) -> None:
    real_secret_client = AzureSecretClient

    def credential_factory() -> _StaticTokenCredential:
        return _StaticTokenCredential()

    def secret_client_factory(
        *,
        vault_url: str,
        credential: TokenCredential,
    ) -> AzureSecretClient:
        assert vault_url == settings.vault_url
        return real_secret_client(
            vault_url=_floci_vault_url(),
            credential=credential,
            transport=_ForceHttpTransport(),
            verify_challenge_resource=False,
        )

    monkeypatch.setattr(key_vault_client_module, 'DefaultAzureCredential', credential_factory)
    monkeypatch.setattr(key_vault_client_module, 'SecretClient', secret_client_factory)


def test_storage_uses_connection_string_retrieved_from_key_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _RUN:
        pytest.skip('Storage Azure-local integration is disabled')

    key_vault_settings = _key_vault_settings()
    _install_floci_key_vault_sdk(monkeypatch, key_vault_settings)

    secret_name = _required_environment('ATLANTICUS_AZURE_LOCAL_STORAGE_SECRET_NAME')
    container_name = _required_environment('ATLANTICUS_AZURE_LOCAL_STORAGE_CONTAINER_NAME')

    with KeyVaultClient(settings=key_vault_settings) as key_vault_client:
        connection_string = key_vault_client.get_secret(secret_name)

    settings = StorageSettings(
        credential=StorageConnectionStringCredential(connection_string=connection_string)
    )
    blob_name = 'smoke/payload.bin'

    with StorageClient(settings=settings) as storage_client:
        assert storage_client.health_check(container_name=container_name)
        storage_client.upload(
            container_name=container_name,
            blob_name=blob_name,
            data=_PAYLOAD,
        )
        assert storage_client.exists(container_name=container_name, blob_name=blob_name)
        assert (
            storage_client.download(container_name=container_name, blob_name=blob_name) == _PAYLOAD
        )
        storage_client.delete(container_name=container_name, blob_name=blob_name)
        assert not storage_client.exists(container_name=container_name, blob_name=blob_name)
