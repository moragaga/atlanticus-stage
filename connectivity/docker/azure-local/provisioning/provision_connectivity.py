"""Provisioning efímero del ecosistema Connectivity para Azure-local."""

from __future__ import annotations

import os
import time

import requests
from azure.core.credentials import AccessToken, TokenCredential
from azure.core.pipeline.transport import HttpRequest, HttpResponse, RequestsTransport
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient

_KEY_VAULT_SMOKE_VALUE = '  atlanticus-azure-local-connectivity  '
_STORAGE_ACCOUNT_KEY = (
    'Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMh0=='
)
_COSMOS_ACCOUNT_KEY = (
    'C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw=='
)
_COSMOS_PARTITION_KEY_PATH = '/scope'
_REDIS_SUBSCRIPTION_ID = '00000000-0000-0000-0000-000000000001'
_REDIS_RESOURCE_GROUP = 'atlanticus-azure-local'
_REDIS_API_VERSION = '2024-11-01'
_REDIS_USERNAME = 'default'
_FLOCI_HTTP_TIMEOUT_SECONDS = 5.0
_REDIS_PROVISIONING_TIMEOUT_SECONDS = 45.0
_REDIS_CREATE_TIMEOUT = (
    _FLOCI_HTTP_TIMEOUT_SECONDS,
    _REDIS_PROVISIONING_TIMEOUT_SECONDS,
)


class _StaticTokenCredential(TokenCredential):
    """Credencial local sin secretos reales; Floci acepta bearer tokens en modo dev."""

    def get_token(self, *scopes: str, **kwargs: object) -> AccessToken:
        del scopes, kwargs
        return AccessToken('atlanticus-azure-local-token', int(time.time()) + 3600)

    def close(self) -> None:
        return


class _ForceHttpTransport(RequestsTransport):
    """Mantiene HTTPS para el SDK Key Vault y usa HTTP sólo hacia Floci local."""

    def send(self, request: HttpRequest, **kwargs: object) -> HttpResponse:
        if request.url.startswith('https://'):
            request.url = f'http://{request.url.removeprefix("https://")}'
        return super().send(request, **kwargs)


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f'{name} is required for Azure-local provisioning.')
    return value


def _target_enabled(name: str) -> bool:
    targets = _required_environment('ATLANTICUS_AZURE_LOCAL_TARGET')
    if targets == 'all':
        return True
    return name in {value.strip() for value in targets.split(',') if value.strip()}


def _endpoint() -> str:
    endpoint = _required_environment('ATLANTICUS_FLOCI_AZ_ENDPOINT').rstrip('/')
    if not endpoint.startswith('http://'):
        raise RuntimeError('ATLANTICUS_FLOCI_AZ_ENDPOINT must use local http:// transport.')
    return endpoint


def _vault_url() -> str:
    endpoint = _endpoint()
    account_name = _required_environment('ATLANTICUS_FLOCI_AZ_ACCOUNT_NAME')
    return f'https://{endpoint.removeprefix("http://")}/{account_name}-keyvault'


def _storage_connection_string() -> str:
    endpoint = _endpoint()
    account_name = _required_environment('ATLANTICUS_FLOCI_AZ_ACCOUNT_NAME')
    return (
        f'DefaultEndpointsProtocol=http;AccountName={account_name};'
        f'AccountKey={_STORAGE_ACCOUNT_KEY};'
        f'BlobEndpoint={endpoint}/{account_name};'
    )


def _cosmos_provisioning_endpoint() -> str:
    account_name = _required_environment('ATLANTICUS_FLOCI_AZ_ACCOUNT_NAME')
    return f'{_endpoint()}/{account_name}-cosmos'


def _redis_arm_url() -> str:
    cache_name = _required_environment('ATLANTICUS_AZURE_LOCAL_REDIS_CACHE_NAME')
    return (
        f'{_endpoint()}/subscriptions/{_REDIS_SUBSCRIPTION_ID}'
        f'/resourceGroups/{_REDIS_RESOURCE_GROUP}'
        f'/providers/Microsoft.Cache/redis/{cache_name}'
    )


def _seed_secret(client: SecretClient, *, name: str, value: str) -> None:
    client.set_secret(name, value)
    print(f'Azure-local Key Vault seeded: {name}')


def _provision_storage() -> str:
    container_name = _required_environment('ATLANTICUS_AZURE_LOCAL_STORAGE_CONTAINER_NAME')
    connection_string = _storage_connection_string()
    client = BlobServiceClient.from_connection_string(connection_string, logging_enable=False)
    try:
        container = client.get_container_client(container_name)
        if not container.exists():
            container.create_container()
    finally:
        client.close()
    print(f'Azure-local Storage provisioned: {container_name}')
    return connection_string


def _require_floci_created_or_exists(response: requests.Response, *, resource: str) -> None:
    if response.status_code in {201, 409}:
        return
    raise RuntimeError(
        f'Azure-local Cosmos provisioning failed for {resource}: HTTP {response.status_code}.'
    )


def _provision_cosmos() -> None:
    """Provisiona Cosmos por REST de Floci sin usar el SDK de Cosmos."""

    database_name = _required_environment('ATLANTICUS_AZURE_LOCAL_COSMOS_DATABASE_NAME')
    container_name = _required_environment('ATLANTICUS_AZURE_LOCAL_COSMOS_CONTAINER_NAME')
    endpoint = _cosmos_provisioning_endpoint()

    with requests.Session() as session:
        database_response = session.post(
            f'{endpoint}/dbs',
            json={'id': database_name},
            timeout=_FLOCI_HTTP_TIMEOUT_SECONDS,
        )
        _require_floci_created_or_exists(
            database_response,
            resource=f'database {database_name}',
        )

        container_response = session.post(
            f'{endpoint}/dbs/{database_name}/colls/',
            json={
                'id': container_name,
                'partitionKey': {
                    'paths': [_COSMOS_PARTITION_KEY_PATH],
                    'kind': 'Hash',
                    'version': 2,
                },
            },
            timeout=_FLOCI_HTTP_TIMEOUT_SECONDS,
        )
        _require_floci_created_or_exists(
            container_response,
            resource=f'container {database_name}/{container_name}',
        )

    print(f'Azure-local Cosmos provisioned: {database_name}/{container_name}')


def _require_redis_properties(payload: object) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise RuntimeError('Azure-local Redis returned an invalid ARM response.')
    properties = payload.get('properties')
    if not isinstance(properties, dict):
        raise RuntimeError('Azure-local Redis ARM response is missing properties.')

    host = properties.get('hostName')
    port = properties.get('port')
    access_keys = properties.get('accessKeys')
    if not isinstance(host, str) or not host:
        raise RuntimeError('Azure-local Redis ARM response is missing hostName.')
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise RuntimeError('Azure-local Redis ARM response contains an invalid port.')
    if not isinstance(access_keys, dict):
        raise RuntimeError('Azure-local Redis ARM response is missing accessKeys.')
    primary_key = access_keys.get('primaryKey')
    if not isinstance(primary_key, str) or not primary_key:
        raise RuntimeError('Azure-local Redis ARM response is missing primaryKey.')
    return f'redis://{host}:{port}', primary_key


def _provision_redis() -> tuple[str, str, str]:
    """Crea un cache real de Floci y retorna valores para la composición Atlanticus."""

    cache_name = _required_environment('ATLANTICUS_AZURE_LOCAL_REDIS_CACHE_NAME')
    arm_url = _redis_arm_url()
    params = {'api-version': _REDIS_API_VERSION}
    body = {
        'location': 'eastus',
        'properties': {
            'sku': {'name': 'Basic', 'family': 'C', 'capacity': 0},
            'enableNonSslPort': True,
            'minimumTlsVersion': '1.2',
        },
    }

    with requests.Session() as session:
        response = session.put(
            arm_url,
            params=params,
            json=body,
            timeout=_REDIS_CREATE_TIMEOUT,
        )
        if response.status_code not in {200, 201}:
            raise RuntimeError(
                f'Azure-local Redis provisioning failed: HTTP {response.status_code}.'
            )

        deadline = time.monotonic() + _REDIS_PROVISIONING_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            status_response = session.get(
                arm_url,
                params=params,
                timeout=_FLOCI_HTTP_TIMEOUT_SECONDS,
            )
            if status_response.status_code != 200:
                raise RuntimeError(
                    f'Azure-local Redis readiness check failed: HTTP {status_response.status_code}.'
                )
            payload = status_response.json()
            properties = payload.get('properties') if isinstance(payload, dict) else None
            state = properties.get('provisioningState') if isinstance(properties, dict) else None
            if state == 'Succeeded':
                url, primary_key = _require_redis_properties(payload)
                print(f'Azure-local Redis provisioned: {cache_name}')
                return url, _REDIS_USERNAME, primary_key
            if state == 'Failed':
                raise RuntimeError('Azure-local Redis provisioning entered Failed state.')
            time.sleep(0.25)

    raise RuntimeError('Azure-local Redis provisioning timed out before Succeeded state.')


def main() -> None:
    credential = _StaticTokenCredential()
    client = SecretClient(
        vault_url=_vault_url(),
        credential=credential,
        transport=_ForceHttpTransport(),
        verify_challenge_resource=False,
    )
    try:
        _seed_secret(
            client,
            name=_required_environment('ATLANTICUS_AZURE_LOCAL_KEY_VAULT_SECRET_NAME'),
            value=_KEY_VAULT_SMOKE_VALUE,
        )
        if _target_enabled('storage'):
            connection_string = _provision_storage()
            _seed_secret(
                client,
                name=_required_environment('ATLANTICUS_AZURE_LOCAL_STORAGE_SECRET_NAME'),
                value=connection_string,
            )
        if _target_enabled('cosmos'):
            _provision_cosmos()
            _seed_secret(
                client,
                name=_required_environment('ATLANTICUS_AZURE_LOCAL_COSMOS_ENDPOINT_SECRET_NAME'),
                value=_endpoint(),
            )
            _seed_secret(
                client,
                name=_required_environment('ATLANTICUS_AZURE_LOCAL_COSMOS_KEY_SECRET_NAME'),
                value=_COSMOS_ACCOUNT_KEY,
            )
        if _target_enabled('redis'):
            redis_url, redis_username, redis_password = _provision_redis()
            _seed_secret(
                client,
                name=_required_environment('ATLANTICUS_AZURE_LOCAL_REDIS_URL_SECRET_NAME'),
                value=redis_url,
            )
            _seed_secret(
                client,
                name=_required_environment('ATLANTICUS_AZURE_LOCAL_REDIS_USERNAME_SECRET_NAME'),
                value=redis_username,
            )
            _seed_secret(
                client,
                name=_required_environment('ATLANTICUS_AZURE_LOCAL_REDIS_PASSWORD_SECRET_NAME'),
                value=redis_password,
            )
    finally:
        client.close()
        credential.close()


if __name__ == '__main__':
    main()
