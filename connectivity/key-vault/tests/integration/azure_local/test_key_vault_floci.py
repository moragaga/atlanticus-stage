from __future__ import annotations

import os
import time

import pytest
from azure.core.credentials import AccessToken, TokenCredential
from azure.core.pipeline.transport import HttpRequest, HttpResponse, RequestsTransport
from azure.keyvault.secrets import SecretClient as AzureSecretClient

import atlanticus.connectivity.key_vault.client as client_module
from atlanticus.connectivity.key_vault import (
    KeyVaultClient,
    KeyVaultSecretNotFoundError,
    KeyVaultSettings,
)
from atlanticus.kernel import Environment

pytestmark = pytest.mark.integration
_RUN = os.getenv('ATLANTICUS_RUN_KEY_VAULT_AZURE_LOCAL_INTEGRATION') == '1'

_EXPECTED_SECRET_VALUE = '  atlanticus-azure-local-connectivity  '


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


def _settings() -> KeyVaultSettings:
    return KeyVaultSettings(
        company_abrev='ATL',
        environment=Environment.from_value('local'),
        product_abrev='TEST',
    )


def _install_floci_sdk(monkeypatch: pytest.MonkeyPatch, settings: KeyVaultSettings) -> None:
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

    monkeypatch.setattr(client_module, 'DefaultAzureCredential', credential_factory)
    monkeypatch.setattr(client_module, 'SecretClient', secret_client_factory)


def test_key_vault_reads_seeded_secret_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    if not _RUN:
        pytest.skip('Key Vault Azure-local integration is disabled')
    settings = _settings()
    _install_floci_sdk(monkeypatch, settings)
    secret_name = _required_environment('ATLANTICUS_AZURE_LOCAL_KEY_VAULT_SECRET_NAME')

    with KeyVaultClient(settings=settings) as client:
        assert client.get_secret(secret_name) == _EXPECTED_SECRET_VALUE


def test_key_vault_maps_missing_secret_without_exposing_transport_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _RUN:
        pytest.skip('Key Vault Azure-local integration is disabled')
    settings = _settings()
    _install_floci_sdk(monkeypatch, settings)

    with KeyVaultClient(settings=settings) as client:
        with pytest.raises(KeyVaultSecretNotFoundError) as captured:
            client.get_secret('atlanticus-secret-that-does-not-exist')

    message = str(captured.value)
    assert 'floci' not in message.lower()
    assert '4577' not in message
