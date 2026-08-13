from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlanticus.connectivity.storage import StorageBlobProperties


def test_blob_properties_are_normalized_and_metadata_is_immutable() -> None:
    item = StorageBlobProperties(
        name='folder/data.parquet',
        size=12,
        etag='etag',
        last_modified=datetime(2026, 8, 13, tzinfo=UTC),
        content_type='application/octet-stream',
        metadata={'kind': 'parquet'},
    )
    assert item.name == 'folder/data.parquet'
    assert item.size == 12
    assert dict(item.metadata) == {'kind': 'parquet'}
    with pytest.raises(TypeError):
        item.metadata['kind'] = 'other'  # type: ignore[index]


@pytest.mark.parametrize('size', [-1, True, 1.5])
def test_blob_properties_reject_invalid_sizes(size: object) -> None:
    with pytest.raises(ValueError, match='size'):
        StorageBlobProperties(name='x', size=size)  # type: ignore[arg-type]


def test_blob_properties_require_name() -> None:
    with pytest.raises(ValueError, match='name'):
        StorageBlobProperties(name='', size=0)
