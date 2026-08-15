from __future__ import annotations

import atlanticus.connectivity.storage as storage


def test_public_api_and_version_are_stable() -> None:
    assert storage.__version__ == '0.1.0'
    expected = {
        'StorageBlobProperties',
        'StorageClient',
        'StorageConnectionStringCredential',
        'StorageSasCredential',
        'StorageSasReader',
        'StorageSasReference',
        'StorageSettings',
    }
    assert expected.issubset(set(storage.__all__))
    assert 'BlobServiceClient' not in storage.__all__
    assert not hasattr(storage, 'BlobServiceClient')
