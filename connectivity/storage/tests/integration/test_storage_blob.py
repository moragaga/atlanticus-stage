from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from io import BytesIO
from time import monotonic, sleep

import pytest
from azure.storage.blob import (
    AccountSasPermissions,
    BlobServiceClient,
    ResourceTypes,
    generate_account_sas,
)

from atlanticus.connectivity.storage import (
    StorageBlobNotFoundError,
    StorageClient,
    StorageConnectionStringCredential,
    StorageSasCredential,
    StorageSettings,
)

pytestmark = pytest.mark.integration

_ACCOUNT_NAME = 'devstoreaccount1'
_ACCOUNT_KEY = (
    'Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=='
)
_CONTAINER = 'atlanticus-storage-integration'


def _require_integration() -> None:
    if os.getenv('ATLANTICUS_RUN_STORAGE_INTEGRATION') != '1':
        pytest.skip('Storage integration is disabled')


def _connection_string() -> str:
    return os.environ['ATLANTICUS_STORAGE_CONNECTION_STRING']


def _account_url() -> str:
    return os.environ['ATLANTICUS_STORAGE_ACCOUNT_URL']


def _wait_until_ready() -> BlobServiceClient:
    deadline = monotonic() + 30
    last_error: BaseException | None = None
    while monotonic() < deadline:
        service = BlobServiceClient.from_connection_string(_connection_string())
        try:
            next(iter(service.list_containers()), None)
            return service
        except Exception as error:
            last_error = error
            service.close()
            sleep(0.5)
    raise RuntimeError('Azurite did not become ready') from last_error


@pytest.fixture(scope='module', autouse=True)
def prepared_storage() -> None:
    _require_integration()
    service = _wait_until_ready()
    container = service.get_container_client(_CONTAINER)
    try:
        container.create_container()
    except Exception as error:
        if getattr(error, 'status_code', None) != 409:
            raise
    try:
        yield
    finally:
        try:
            container.delete_container()
        finally:
            service.close()


def _connection_client() -> StorageClient:
    return StorageClient(
        settings=StorageSettings(
            credential=StorageConnectionStringCredential(connection_string=_connection_string())
        )
    )


def _sas_client() -> StorageClient:
    token = generate_account_sas(
        account_name=_ACCOUNT_NAME,
        account_key=_ACCOUNT_KEY,
        resource_types=ResourceTypes(service=True, container=True, object=True),
        permission=AccountSasPermissions(
            read=True,
            write=True,
            delete=True,
            list=True,
            create=True,
            add=True,
        ),
        expiry=datetime.now(UTC) + timedelta(hours=1),
    )
    return StorageClient(
        settings=StorageSettings(
            credential=StorageSasCredential(
                account_url=_account_url(),
                sas_token=token,
                allow_insecure_http=True,
            )
        )
    )


@pytest.mark.parametrize('factory', [_connection_client, _sas_client])
def test_connection_string_and_sas_support_full_blob_roundtrip(factory) -> None:
    blob_name = f'{factory.__name__}/payload.bin'
    with factory() as client:
        assert client.health_check(container_name=_CONTAINER) is True
        client.upload(
            container_name=_CONTAINER,
            blob_name=blob_name,
            data=b'atlanticus-storage',
            metadata={'source': 'integration'},
            content_type='application/octet-stream',
        )
        assert client.exists(container_name=_CONTAINER, blob_name=blob_name) is True
        assert (
            client.download(container_name=_CONTAINER, blob_name=blob_name) == b'atlanticus-storage'
        )
        properties = client.get_properties(container_name=_CONTAINER, blob_name=blob_name)
        assert properties.size == len(b'atlanticus-storage')
        assert properties.content_type == 'application/octet-stream'
        assert dict(properties.metadata) == {'source': 'integration'}
        client.delete(container_name=_CONTAINER, blob_name=blob_name)
        assert client.exists(container_name=_CONTAINER, blob_name=blob_name) is False


def test_stream_download_and_prefix_listing() -> None:
    with _connection_client() as client:
        for name in ('dataset/a.bin', 'dataset/b.bin', 'other/c.bin'):
            client.upload(container_name=_CONTAINER, blob_name=name, data=name.encode())
        names = tuple(
            item.name
            for item in client.list_blobs(
                container_name=_CONTAINER,
                prefix='dataset/',
                max_items=10,
            )
        )
        assert names == ('dataset/a.bin', 'dataset/b.bin')
        target = BytesIO()
        count = client.download_to(
            container_name=_CONTAINER,
            blob_name='dataset/a.bin',
            target=target,
        )
        assert count == len(b'dataset/a.bin')
        assert target.getvalue() == b'dataset/a.bin'


def test_missing_blob_is_sanitized() -> None:
    with _connection_client() as client:
        with pytest.raises(StorageBlobNotFoundError) as captured:
            client.download(container_name=_CONTAINER, blob_name='missing/private.bin')
        assert 'http://' not in repr(captured.value)
        assert captured.value.__cause__ is None
