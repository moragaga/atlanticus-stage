from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest

from atlanticus.connectivity.storage import (
    StorageBlobNotFoundError,
    StorageConfigurationError,
    StorageSasReader,
    StorageSasReference,
)


class FakeHttpResponseError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeServiceRequestError(Exception):
    pass


class FakeServiceResponseError(Exception):
    pass


class FakeDownload:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def readall(self) -> bytes:
        return self.data

    def readinto(self, target: Any) -> int:
        return int(target.write(self.data))


class FakeBlobClient:
    created: list[dict[str, Any]] = []
    data = b'payload'
    error: BaseException | None = None

    @classmethod
    def from_blob_url(cls, **values: Any) -> FakeBlobClient:
        cls.created.append(values)
        return cls()

    def download_blob(self) -> FakeDownload:
        if self.error is not None:
            raise self.error
        return FakeDownload(self.data)

    def close(self) -> None:
        pass


def _reader() -> StorageSasReader:
    FakeBlobClient.created.clear()
    FakeBlobClient.error = None
    reader = StorageSasReader()
    reader._sdk = SimpleNamespace(
        BlobClient=FakeBlobClient,
        HttpResponseError=FakeHttpResponseError,
        ServiceRequestError=FakeServiceRequestError,
        ServiceResponseError=FakeServiceResponseError,
    )
    return reader


def test_sas_reference_accepts_signed_url_without_exposing_secret() -> None:
    reference = StorageSasReference.from_values(
        sas_url='https://account.blob.core.windows.net/container/blob.parquet?sig=secret&sp=r'
    )

    assert reference.url == 'https://account.blob.core.windows.net/container/blob.parquet'
    assert reference.sas_token == 'sig=secret&sp=r'
    assert 'secret' not in repr(reference)


def test_sas_reference_accepts_url_and_token_separately() -> None:
    reference = StorageSasReference.from_values(
        sas_url='https://account.blob.core.windows.net/container',
        sas_token='?sig=secret',
        blob_name='folder/blob.parquet',
    )

    assert reference.url == 'https://account.blob.core.windows.net/container/folder/blob.parquet'
    assert reference.sas_token == 'sig=secret'


def test_sas_reference_rejects_missing_or_conflicting_token() -> None:
    with pytest.raises(StorageConfigurationError, match='sas_token is required'):
        StorageSasReference.from_values(
            sas_url='https://account.blob.core.windows.net/container/blob.parquet'
        )
    with pytest.raises(StorageConfigurationError, match='contain different values'):
        StorageSasReference.from_values(
            sas_url='https://account.blob.core.windows.net/container/blob.parquet?sig=one',
            sas_token='sig=two',
        )


def test_sas_reader_downloads_to_stream_and_closes_ephemeral_client() -> None:
    reader = _reader()
    reference = StorageSasReference.from_values(
        sas_url='https://account.blob.core.windows.net/container/blob.parquet',
        sas_token='sig=secret',
    )
    target = BytesIO()

    size = reader.download_to(reference=reference, target=target)

    assert size == len(b'payload')
    assert target.getvalue() == b'payload'
    created = FakeBlobClient.created[0]
    assert created['blob_url'].endswith('blob.parquet?sig=secret')
    assert created['connection_timeout'] == 20
    assert created['read_timeout'] == 60
    assert created['logging_enable'] is False


def test_sas_reader_maps_not_found_without_exposing_url() -> None:
    reader = _reader()
    FakeBlobClient.error = FakeHttpResponseError(404)
    reference = StorageSasReference.from_values(
        sas_url='https://account.blob.core.windows.net/container/private.parquet',
        sas_token='sig=private',
    )

    with pytest.raises(StorageBlobNotFoundError, match='Storage blob not found') as captured:
        reader.download(reference=reference)

    assert 'private.parquet' not in str(captured.value)
    assert 'sig=private' not in str(captured.value)
