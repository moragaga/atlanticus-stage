"""Provisioning efímero del ecosistema Connectivity para Azure-local."""

from __future__ import annotations

import os
import time

from azure.core.credentials import AccessToken, TokenCredential
from azure.core.pipeline.transport import HttpRequest, HttpResponse, RequestsTransport
from azure.cosmos import CosmosClient as AzureCosmosClient
from azure.cosmos import PartitionKey
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient

_KEY_VAULT_SMOKE_VALUE = '  atlanticus-azure-local-connectivity  '
_STORAGE_ACCOUNT_KEY = (
    'Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/'
    'K1SZFPTOtr/KBHBeksoGMh0=='
)
_COSMOS_ACCOUNT_KEY = (
    'C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPM'
    'bIZnqyMsEcaGQy67XIw/Jw=='
)
_COSMOS_PARTITION_KEY_PATH = '/scope'


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
            request.url = f"http://{request.url.removeprefix('https://')}"
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
    return f"https://{endpoint.removeprefix('http://')}/{account_name}-keyvault"


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


def _provision_cosmos() -> None:
    database_name = _required_environment('ATLANTICUS_AZURE_LOCAL_COSMOS_DATABASE_NAME')
    container_name = _required_environment('ATLANTICUS_AZURE_LOCAL_COSMOS_CONTAINER_NAME')
    client = AzureCosmosClient(
        url=_cosmos_provisioning_endpoint(),
        credential=_COSMOS_ACCOUNT_KEY,
        connection_mode='Gateway',
        retry_write=0,
    )
    try:
        database_ids = {item['id'] for item in client.list_databases()}
        if database_name in database_ids:
            database = client.get_database_client(database_name)
        else:
            database = client.create_database(database_name)

        container_ids = {item['id'] for item in database.list_containers()}
        if container_name not in container_ids:
            database.create_container(
                container_name,
                partition_key=PartitionKey(path=_COSMOS_PARTITION_KEY_PATH),
            )
    finally:
        client.close()
    print(f'Azure-local Cosmos provisioned: {database_name}/{container_name}')


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
    finally:
        client.close()
        credential.close()


if __name__ == '__main__':
    main()
