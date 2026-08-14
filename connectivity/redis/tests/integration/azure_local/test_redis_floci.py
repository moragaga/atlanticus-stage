from __future__ import annotations

import os
import time

import pytest
from azure.core.credentials import AccessToken, TokenCredential
from azure.core.pipeline.transport import HttpRequest, HttpResponse, RequestsTransport
from azure.keyvault.secrets import SecretClient as AzureSecretClient

import atlanticus.connectivity.key_vault.client as key_vault_client_module
from atlanticus.connectivity.key_vault import KeyVaultClient, KeyVaultSettings
from atlanticus.connectivity.redis import RedisClient, RedisSettings
from atlanticus.kernel import Environment

pytestmark = pytest.mark.integration
_RUN = os.getenv('ATLANTICUS_RUN_REDIS_AZURE_LOCAL_INTEGRATION') == '1'
_PREFIX = 'atlanticus:azure-local:'


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


def test_redis_contract_is_composed_from_key_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _RUN:
        pytest.skip('Redis Azure-local integration is disabled')

    key_vault_settings = _key_vault_settings()
    _install_floci_key_vault_sdk(monkeypatch, key_vault_settings)

    url_secret_name = _required_environment('ATLANTICUS_AZURE_LOCAL_REDIS_URL_SECRET_NAME')
    username_secret_name = _required_environment(
        'ATLANTICUS_AZURE_LOCAL_REDIS_USERNAME_SECRET_NAME'
    )
    password_secret_name = _required_environment(
        'ATLANTICUS_AZURE_LOCAL_REDIS_PASSWORD_SECRET_NAME'
    )

    with KeyVaultClient(settings=key_vault_settings) as key_vault_client:
        url = key_vault_client.get_secret(url_secret_name)
        username = key_vault_client.get_secret(username_secret_name)
        password = key_vault_client.get_secret(password_secret_name)

    settings = RedisSettings(
        url=url,
        username=username,
        password=password,
        database=0,
        allow_insecure_transport=True,
        connection_timeout_seconds=2,
        operation_timeout_seconds=2,
        max_connections=4,
        max_mget_keys=20,
    )
    key_a = f'{_PREFIX}a'
    key_b = f'{_PREFIX}b'
    missing = f'{_PREFIX}missing'

    with RedisClient(settings=settings) as redis_client:
        redis_client.delete(key=key_a)
        redis_client.delete(key=key_b)
        redis_client.delete(key=missing)

        assert redis_client.health_check() is True
        redis_client.set(key=key_a, value=b'atlanticus')
        redis_client.set(key=key_b, value=b'azure-local')
        assert redis_client.exists(key=key_a) is True
        assert redis_client.get(key=key_a) == b'atlanticus'
        assert redis_client.mget(keys=[key_a, missing, key_b]) == (
            b'atlanticus',
            None,
            b'azure-local',
        )

        assert redis_client.expire(key=key_a, ttl_seconds=30) is True
        ttl = redis_client.ttl(key=key_a)
        assert ttl.exists is True
        assert ttl.seconds is not None
        assert 0 <= ttl.seconds <= 30

        assert redis_client.delete(key=key_a) is True
        assert redis_client.delete(key=key_b) is True
        assert redis_client.exists(key=key_a) is False
