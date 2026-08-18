from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from atlanticus.connectivity.storage import (
    StorageBlobProperties,
    StorageClient,
    StorageConnectionStringCredential,
    StorageSettings,
)
from atlanticus.data_producers.remanentes import RemanentesStorageSource

from .support import build_test_catalog


class _StorageClient(StorageClient):
    def __init__(
        self,
        values: dict[str, tuple[StorageBlobProperties, ...]],
        *,
        download_path: Path | None = None,
    ) -> None:
        super().__init__(
            settings=StorageSettings(
                credential=StorageConnectionStringCredential('UseDevelopmentStorage=true')
            )
        )
        self.values = values
        self.download_path = download_path

    def list_blobs(
        self,
        *,
        container_name: str,
        prefix: str | None = None,
        **_: object,
    ) -> tuple[StorageBlobProperties, ...]:
        assert container_name == 'dataproduct'
        return self.values.get(prefix or '', ())

    def download_to(self, *, container_name: str, blob_name: str, target) -> int:
        assert container_name == 'dataproduct'
        assert blob_name
        assert self.download_path is not None
        payload = self.download_path.read_bytes()
        target.write(payload)
        return len(payload)


def _definition():
    return build_test_catalog()[0]


def _item(name: str, etag: str | None, *, minute: int = 0) -> StorageBlobProperties:
    return StorageBlobProperties(
        name=name,
        size=100,
        etag=etag,
        last_modified=datetime(2026, 8, 11, 1, minute, tzinfo=UTC),
    )


def test_pending_continues_after_cursor_in_source_order() -> None:
    definition = _definition()
    prefix = 'remanentes/stocks/year=2026/month=08/day=10/'
    source = RemanentesStorageSource(
        client=_StorageClient(
            {
                prefix: (
                    _item(f'{prefix}data_20260810_2050.parquet', 'c'),
                    _item(f'{prefix}data_20260810_2030.parquet', 'a'),
                    _item(f'{prefix}data_20260810_2040.parquet', 'b'),
                )
            }
        ),
        container_name='dataproduct',
        definition=definition,
    )

    pending = source.pending(
        now_utc=datetime(2026, 8, 11, 1, 5, tzinfo=UTC),
        cursor_timestamp_utc=datetime(2026, 8, 11, 0, 40, tzinfo=UTC),
        cursor_blob_name=f'{prefix}data_20260810_2040.parquet',
        cursor_blob_etag='b',
        cursor_blob_last_modified_utc=None,
    )

    assert tuple(item.name for item in pending) == (f'{prefix}data_20260810_2050.parquet',)


def test_same_cursor_is_reprocessed_when_etag_changes() -> None:
    definition = _definition()
    prefix = 'remanentes/stocks/year=2026/month=08/day=10/'
    name = f'{prefix}data_20260810_2040.parquet'
    source = RemanentesStorageSource(
        client=_StorageClient({prefix: (_item(name, 'new'),)}),
        container_name='dataproduct',
        definition=definition,
    )

    pending = source.pending(
        now_utc=datetime(2026, 8, 11, 1, 5, tzinfo=UTC),
        cursor_timestamp_utc=datetime(2026, 8, 11, 0, 40, tzinfo=UTC),
        cursor_blob_name=name,
        cursor_blob_etag='old',
        cursor_blob_last_modified_utc=None,
    )

    assert tuple(item.name for item in pending) == (name,)


def test_same_cursor_uses_last_modified_when_etag_is_unavailable() -> None:
    definition = _definition()
    prefix = 'remanentes/stocks/year=2026/month=08/day=10/'
    name = f'{prefix}data_20260810_2040.parquet'
    source = RemanentesStorageSource(
        client=_StorageClient({prefix: (_item(name, None, minute=2),)}),
        container_name='dataproduct',
        definition=definition,
    )

    pending = source.pending(
        now_utc=datetime(2026, 8, 11, 1, 5, tzinfo=UTC),
        cursor_timestamp_utc=datetime(2026, 8, 11, 0, 40, tzinfo=UTC),
        cursor_blob_name=name,
        cursor_blob_etag=None,
        cursor_blob_last_modified_utc=datetime(2026, 8, 11, 1, 1, tzinfo=UTC),
    )

    assert tuple(item.name for item in pending) == (name,)


def test_download_reads_stock_columns_case_insensitively(tmp_path: Path) -> None:
    path = tmp_path / 'source.parquet'
    pq.write_table(
        pa.table({'stock': ['STOC_2960'], 'ton (KT)': [27], 'ignored': ['x']}),
        path,
    )
    source = RemanentesStorageSource(
        client=_StorageClient({}, download_path=path),
        container_name='dataproduct',
        definition=_definition(),
    )

    table = source.download_table(blob_name='source.parquet')

    assert table.column_names == ['STOCK', 'Ton (kt)']
    assert table.num_rows == 1


def test_download_rows_uses_fixed_legacy_source_contract(tmp_path: Path) -> None:
    path = tmp_path / 'source.parquet'
    pq.write_table(
        pa.table(
            {
                'fase': ['F11W'],
                'BANCO': [3080],
                'tipo DE material': ['Mineral'],
                'Observación': ['Rem. Extraíble'],
                'Ton (kt)': [39],
            }
        ),
        path,
    )
    source = RemanentesStorageSource(
        client=_StorageClient({}, download_path=path),
        container_name='dataproduct',
        definition=build_test_catalog()[1],
    )

    table = source.download_table(blob_name='source.parquet')

    assert table.column_names == [
        'Fase',
        'Banco',
        'Tipo de material',
        'Observación',
        'Ton (kt)',
    ]
