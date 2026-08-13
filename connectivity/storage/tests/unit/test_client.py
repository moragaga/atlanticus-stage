from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest

from atlanticus.connectivity.storage import (
    StorageAuthenticationError,
    StorageBlobNotFoundError,
    StorageClient,
    StorageClosedError,
    StorageConnectionError,
    StorageConnectionStringCredential,
    StorageContainerNotFoundError,
    StorageResultLimitError,
    StorageSasCredential,
    StorageSettings,
)
from atlanticus.connectivity.storage.client import _StorageSdk


class FakeHttpError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__('private-url?sig=secret')


class FakeRequestError(Exception):
    pass


class FakeResponseError(Exception):
    pass


class FakeContentSettings:
    def __init__(self, *, content_type: str) -> None:
        self.content_type = content_type


class FakeDownload:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def readall(self) -> bytes:
        return self.data

    def readinto(self, target: BytesIO) -> int:
        target.write(self.data)
        return len(self.data)


class FakeBlob:
    def __init__(self, name: str, store: dict[str, dict[str, object]]) -> None:
        self.name = name
        self.store = store
        self.error: BaseException | None = None

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    def exists(self) -> bool:
        self._raise()
        return self.name in self.store

    def upload_blob(self, data: object, **kwargs: object) -> None:
        self._raise()
        if hasattr(data, 'read'):
            payload = data.read()
        else:
            payload = bytes(data)  # type: ignore[arg-type]
        if not kwargs['overwrite'] and self.name in self.store:
            raise FakeHttpError(409)
        content_settings = kwargs.get('content_settings')
        self.store[self.name] = {
            'data': payload,
            'metadata': kwargs.get('metadata') or {},
            'content_type': getattr(content_settings, 'content_type', None),
        }

    def download_blob(self) -> FakeDownload:
        self._raise()
        if self.name not in self.store:
            raise FakeHttpError(404)
        return FakeDownload(self.store[self.name]['data'])  # type: ignore[arg-type]

    def delete_blob(self) -> None:
        self._raise()
        if self.name not in self.store:
            raise FakeHttpError(404)
        del self.store[self.name]

    def get_blob_properties(self) -> object:
        self._raise()
        if self.name not in self.store:
            raise FakeHttpError(404)
        item = self.store[self.name]
        return SimpleNamespace(
            size=len(item['data']),  # type: ignore[arg-type]
            etag='etag',
            last_modified=None,
            metadata=item['metadata'],
            content_settings=SimpleNamespace(content_type=item['content_type']),
        )


class FakeContainer:
    def __init__(self, name: str, service: 'FakeService') -> None:
        self.name = name
        self.service = service

    def exists(self) -> bool:
        return self.name in self.service.containers

    def list_blobs(self, *, name_starts_with: str | None = None):
        if self.name not in self.service.containers:
            raise FakeHttpError(404)
        for name, item in sorted(self.service.containers[self.name].items()):
            if name_starts_with is not None and not name.startswith(name_starts_with):
                continue
            yield SimpleNamespace(
                name=name,
                size=len(item['data']),
                etag='etag',
                last_modified=None,
                metadata=item['metadata'],
                content_settings=SimpleNamespace(content_type=item['content_type']),
            )


class FakeService:
    connection_calls: list[tuple[str, dict[str, object]]] = []
    sas_calls: list[tuple[str, str, dict[str, object]]] = []

    def __init__(
        self, *, account_url: str | None = None, credential: str | None = None, **kwargs: object
    ) -> None:
        self.account_url = account_url
        self.credential = credential
        self.kwargs = kwargs
        self.containers: dict[str, dict[str, dict[str, object]]] = {'data': {}}
        self.closed = False
        self.close_error = False
        if account_url is not None:
            self.sas_calls.append((account_url, credential or '', kwargs))

    @classmethod
    def from_connection_string(cls, connection_string: str, **kwargs: object) -> 'FakeService':
        cls.connection_calls.append((connection_string, kwargs))
        return cls(**kwargs)

    def get_container_client(self, name: str) -> FakeContainer:
        return FakeContainer(name, self)

    def get_blob_client(self, *, container: str, blob: str) -> FakeBlob:
        if container not in self.containers:
            self.containers[container] = {}
        return FakeBlob(blob, self.containers[container])

    def close(self) -> None:
        self.closed = True
        if self.close_error:
            raise RuntimeError('private close error')


def _sdk() -> _StorageSdk:
    return _StorageSdk(
        BlobServiceClient=FakeService,
        ContentSettings=FakeContentSettings,
        HttpResponseError=FakeHttpError,
        ServiceRequestError=FakeRequestError,
        ServiceResponseError=FakeResponseError,
    )


def _connection_client() -> StorageClient:
    FakeService.connection_calls.clear()
    client = StorageClient(
        settings=StorageSettings(
            credential=StorageConnectionStringCredential(
                connection_string='DefaultEndpointsProtocol=https;AccountKey=private-secret'
            ),
            connection_timeout_seconds=7,
            read_timeout_seconds=11,
            max_list_items=2,
        )
    )
    client._sdk = _sdk()
    return client


def test_connection_string_client_is_lazy_reused_and_closed() -> None:
    client = _connection_client()
    assert FakeService.connection_calls == []
    assert client.health_check(container_name='data') is True
    assert client.health_check(container_name='data') is True
    assert len(FakeService.connection_calls) == 1
    connection_string, kwargs = FakeService.connection_calls[0]
    assert 'private-secret' in connection_string
    assert kwargs == {'connection_timeout': 7, 'read_timeout': 11, 'logging_enable': False}
    service = client._client
    client.close()
    assert service.closed is True
    with pytest.raises(StorageClosedError):
        client.health_check(container_name='data')


def test_sas_client_uses_account_url_and_secret_without_exposing_it() -> None:
    FakeService.sas_calls.clear()
    client = StorageClient(
        settings=StorageSettings(
            credential=StorageSasCredential(
                account_url='https://account.blob.core.windows.net',
                sas_token='sv=1&sig=private',
            )
        )
    )
    client._sdk = _sdk()
    client.open()
    assert FakeService.sas_calls[0][0] == 'https://account.blob.core.windows.net'
    assert FakeService.sas_calls[0][1] == 'sv=1&sig=private'


def test_upload_download_properties_list_delete_and_stream_download() -> None:
    client = _connection_client()
    client.upload(
        container_name='data',
        blob_name='folder/a.bin',
        data=BytesIO(b'abc'),
        metadata={'kind': 'test'},
        content_type='application/octet-stream',
    )
    assert client.exists(container_name='data', blob_name='folder/a.bin') is True
    assert client.download(container_name='data', blob_name='folder/a.bin') == b'abc'
    target = BytesIO()
    assert client.download_to(container_name='data', blob_name='folder/a.bin', target=target) == 3
    assert target.getvalue() == b'abc'
    properties = client.get_properties(container_name='data', blob_name='folder/a.bin')
    assert properties.size == 3
    assert properties.content_type == 'application/octet-stream'
    assert dict(properties.metadata) == {'kind': 'test'}
    listed = client.list_blobs(container_name='data', prefix='folder/')
    assert tuple(item.name for item in listed) == ('folder/a.bin',)
    client.delete(container_name='data', blob_name='folder/a.bin')
    assert client.exists(container_name='data', blob_name='folder/a.bin') is False


def test_list_has_strict_limit() -> None:
    client = _connection_client()
    for index in range(3):
        client.upload(container_name='data', blob_name=f'{index}.txt', data=b'x')
    with pytest.raises(StorageResultLimitError) as captured:
        client.list_blobs(container_name='data')
    assert captured.value.max_items == 2


def test_missing_container_and_blob_are_classified() -> None:
    client = _connection_client()
    with pytest.raises(StorageContainerNotFoundError):
        client.health_check(container_name='missing')
    with pytest.raises(StorageBlobNotFoundError):
        client.download(container_name='data', blob_name='missing')


def test_http_and_network_errors_are_sanitized() -> None:
    client = _connection_client()
    service = client._get_client()
    blob = service.get_blob_client(container='data', blob='x')
    blob.error = FakeHttpError(401)
    service.get_blob_client = lambda **_: blob
    with pytest.raises(StorageAuthenticationError) as captured:
        client.download(container_name='data', blob_name='x')
    assert 'private' not in repr(captured.value)
    assert captured.value.__cause__ is None

    blob.error = FakeRequestError('https://storage?sig=private')
    with pytest.raises(StorageConnectionError) as captured:
        client.download(container_name='data', blob_name='x')
    assert 'private' not in repr(captured.value)


def test_context_manager_does_not_hide_business_error_on_close_failure() -> None:
    client = _connection_client()
    client.open()
    client._client.close_error = True
    with pytest.raises(ValueError, match='business failure'):
        with client:
            raise ValueError('business failure')
