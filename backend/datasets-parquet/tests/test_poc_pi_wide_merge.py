from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier, BrokenBarrierError

import pyarrow as pa
import pytest
from parquet_test_helpers import timestamp_array

from atlanticus.datasets import DatasetDefinition, PublicationStatus
from atlanticus.datasets.parquet import (
    ParquetDatasetStore,
    ParquetSchemaError,
    ParquetValidationError,
)


def _target(definition: DatasetDefinition):
    return definition.resolve_target(
        materialization='granular',
        partition={'year': '2026', 'month': '07', 'day': '21'},
    )


def test_poc_pi_wide_merge_uses_incoming_schema_and_nulls_win(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(pi_definition)
    initial = pa.table(
        {
            'timestamp': timestamp_array(
                '2026-07-21T10:00:00Z',
                '2026-07-21T11:00:00Z',
            ),
            'tk10_nivel_inst': pa.array([10.0, 11.0]),
            'retired_tag': pa.array([100.0, 110.0]),
        }
    )
    incoming = pa.table(
        {
            'timestamp': timestamp_array(
                '2026-07-21T11:00:00Z',
                '2026-07-21T12:00:00Z',
            ),
            'tk10_nivel_inst': pa.array([None, 12.0], type=pa.float64()),
            'tk12_nivel_inst': pa.array([21.0, 22.0]),
        }
    )
    store.replace(definition=pi_definition, target=target, table=initial)

    result = store.merge(
        definition=pi_definition,
        target=target,
        incoming=incoming,
        key_columns=('timestamp',),
        order_by=('timestamp',),
    )
    merged = store.read(definition=pi_definition, target=target).table

    assert result.status is PublicationStatus.COMMITTED
    assert merged.column_names == ['timestamp', 'tk10_nivel_inst', 'tk12_nivel_inst']
    assert merged['tk10_nivel_inst'].to_pylist() == [10.0, None, 12.0]
    assert merged['tk12_nivel_inst'].to_pylist() == [None, 21.0, 22.0]
    assert 'retired_tag' not in merged.column_names

    repeated = store.merge(
        definition=pi_definition,
        target=target,
        incoming=incoming,
        key_columns=('timestamp',),
        order_by=('timestamp',),
    )
    assert repeated.status is PublicationStatus.UNCHANGED
    assert repeated.content_signature == result.content_signature


def test_concurrent_merges_on_the_same_store_preserve_both_updates(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(pi_definition)
    store.replace(
        definition=pi_definition,
        target=target,
        table=pa.table({'id': [0], 'value': [0]}),
    )
    write_barrier = Barrier(2)
    original_replace_table = store._replace_table

    def synchronized_replace_table(**kwargs):
        try:
            write_barrier.wait(timeout=0.2)
        except BrokenBarrierError:
            pass
        return original_replace_table(**kwargs)

    monkeypatch.setattr(store, '_replace_table', synchronized_replace_table)

    def merge_row(identifier: int):
        return store.merge(
            definition=pi_definition,
            target=target,
            incoming=pa.table({'id': [identifier], 'value': [identifier]}),
            key_columns=('id',),
            order_by=('id',),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(merge_row, (1, 2)))

    assert all(result.status is PublicationStatus.COMMITTED for result in results)
    assert store.read(definition=pi_definition, target=target).table.to_pydict() == {
        'id': [0, 1, 2],
        'value': [0, 1, 2],
    }


def test_merge_rejects_missing_or_null_keys(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(pi_definition)

    with pytest.raises(ParquetSchemaError):
        store.merge(
            definition=pi_definition,
            target=target,
            incoming=pa.table({'value': [1]}),
            key_columns=('timestamp',),
        )

    with pytest.raises(ParquetValidationError):
        store.merge(
            definition=pi_definition,
            target=target,
            incoming=pa.table(
                {
                    'timestamp': pa.array([None], type=pa.timestamp('us', tz='UTC')),
                    'value': [1],
                }
            ),
            key_columns=('timestamp',),
        )
