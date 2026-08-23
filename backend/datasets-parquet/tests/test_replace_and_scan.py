from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pytest
from parquet_test_helpers import timestamp_array

import atlanticus.datasets.parquet._publication as publication_module
import atlanticus.datasets.parquet._scan as scan_module
import atlanticus.datasets.parquet.store as store_module
from atlanticus.datasets import DatasetDefinition, PublicationSkipReason, PublicationStatus
from atlanticus.datasets.parquet import (
    ColumnFilter,
    FilterOperator,
    ParquetCorruptionError,
    ParquetDatasetStore,
    ParquetPublicationNotFoundError,
    ParquetSchemaError,
    ParquetValidationError,
    ParquetWriteError,
)


def _target(definition: DatasetDefinition, *, day: str = '21'):
    return definition.resolve_target(
        materialization='granular',
        partition={'year': '2026', 'month': '07', 'day': day},
    )


def _store(tmp_path: Path, clock: datetime) -> ParquetDatasetStore:
    return ParquetDatasetStore(root=tmp_path / 'data', clock=lambda: clock)


def test_path_is_derived_only_from_validated_target(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
) -> None:
    store = _store(tmp_path, clock)
    target = _target(pi_definition)

    assert store.path_for(definition=pi_definition, target=target) == (
        tmp_path
        / 'data'
        / 'pi'
        / 'pi-web-api'
        / 'recorded'
        / 'process'
        / 'granular'
        / 'year=2026'
        / 'month=07'
        / 'day=21'
    )


def test_empty_replace_is_skipped_without_creating_the_target(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
) -> None:
    store = _store(tmp_path, clock)
    target = _target(pi_definition)
    table = pa.table({'timestamp': timestamp_array()})

    result = store.replace(definition=pi_definition, target=target, table=table)

    assert result.status is PublicationStatus.SKIPPED
    assert result.skip_reason is PublicationSkipReason.EMPTY_CONTENT
    assert not store.path_for(definition=pi_definition, target=target).exists()


def test_replace_round_trip_and_scan_apply_projection_and_time_filter(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
) -> None:
    store = _store(tmp_path, clock)
    target = _target(pi_definition)
    table = pa.table(
        {
            'timestamp': timestamp_array(
                '2026-07-21T10:00:00Z',
                '2026-07-21T11:00:00Z',
                '2026-07-21T12:00:00Z',
            ),
            'tk10_nivel_inst': pa.array([10.0, 11.0, 12.0]),
            'unused': pa.array([100, 200, 300], type=pa.int64()),
        }
    )

    publication = store.replace(definition=pi_definition, target=target, table=table)
    complete = store.read(definition=pi_definition, target=target)
    projected = store.scan(
        definition=pi_definition,
        targets=(target,),
        columns=('timestamp', 'tk10_nivel_inst'),
        filters=(
            ColumnFilter(
                column='timestamp',
                operator=FilterOperator.GREATER_THAN_OR_EQUAL,
                value=clock.replace(hour=11),
            ),
            ColumnFilter(
                column='timestamp',
                operator=FilterOperator.LESS_THAN,
                value=clock.replace(hour=12),
            ),
        ),
    )

    assert publication.status is PublicationStatus.COMMITTED
    assert complete.table.equals(table)
    assert projected.table.column_names == ['timestamp', 'tk10_nivel_inst']
    assert projected.table['tk10_nivel_inst'].to_pylist() == [11.0]
    assert projected.artifact_count == 1


def test_identical_replace_is_unchanged(
    tmp_path: Path,
    pi_definition: DatasetDefinition,
    clock: datetime,
) -> None:
    store = _store(tmp_path, clock)
    target = _target(pi_definition)
    table = pa.table(
        {
            'timestamp': timestamp_array('2026-07-21T10:00:00Z'),
            'value': [1],
        }
    )

    first = store.replace(definition=pi_definition, target=target, table=table)
    second = store.replace(definition=pi_definition, target=target, table=table)

    assert first.status is PublicationStatus.COMMITTED
    assert second.status is PublicationStatus.UNCHANGED
    assert second.content_signature == first.content_signature


def test_failed_atomic_replace_preserves_the_previous_parquet(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, clock)
    target = _target(pi_definition)
    initial = pa.table({'timestamp': timestamp_array('2026-07-21T10:00:00Z'), 'value': [1]})
    replacement = pa.table({'timestamp': timestamp_array('2026-07-21T11:00:00Z'), 'value': [2]})
    store.replace(definition=pi_definition, target=target, table=initial)
    original_replace = store_module.os.replace

    def fail_data_replace(source: Path, destination: Path) -> None:
        if Path(destination).name == 'data.parquet':
            raise OSError('controlled replace failure')
        original_replace(source, destination)

    monkeypatch.setattr(store_module.os, 'replace', fail_data_replace)

    with pytest.raises(ParquetWriteError):
        store.replace(definition=pi_definition, target=target, table=replacement)

    assert store.read(definition=pi_definition, target=target).table.equals(initial)
    target_path = store.path_for(definition=pi_definition, target=target)
    assert not tuple(target_path.glob('.*.tmp'))


def test_replace_does_not_override_filesystem_permissions(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, clock)
    target = _target(pi_definition)

    def reject_chmod(*_args: object, **_kwargs: object) -> None:
        raise AssertionError('the parquet store must not call chmod')

    monkeypatch.setattr(store_module.os, 'chmod', reject_chmod)

    store.replace(
        definition=pi_definition,
        target=target,
        table=pa.table({'timestamp': timestamp_array('2026-07-21T10:00:00Z')}),
    )

    assert store.read(definition=pi_definition, target=target).row_count == 1


def test_missing_publication_is_not_treated_as_an_empty_table(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
) -> None:
    with pytest.raises(ParquetPublicationNotFoundError):
        _store(tmp_path, clock).read(
            definition=pi_definition,
            target=_target(pi_definition),
        )


def test_multiple_targets_require_explicit_columns_and_align_new_columns(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
) -> None:
    store = _store(tmp_path, clock)
    day_20 = _target(pi_definition, day='20')
    day_21 = _target(pi_definition, day='21')
    store.replace(
        definition=pi_definition,
        target=day_20,
        table=pa.table({'timestamp': timestamp_array('2026-07-20T23:00:00Z'), 'value': [1.0]}),
    )
    store.replace(
        definition=pi_definition,
        target=day_21,
        table=pa.table(
            {
                'timestamp': timestamp_array('2026-07-21T01:00:00Z'),
                'value': [2.0],
                'new_tag': pa.array([20.0]),
            }
        ),
    )

    with pytest.raises(ParquetValidationError):
        store.scan(definition=pi_definition, targets=(day_20, day_21))

    result = store.scan(
        definition=pi_definition,
        targets=(day_20, day_21),
        columns=('timestamp', 'new_tag'),
    )

    assert result.table['new_tag'].to_pylist() == [None, 20.0]
    assert result.target_count == 2
    assert result.artifact_count == 2
    assert len(result.warnings) == 1


def test_multiple_targets_reject_incompatible_types(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
) -> None:
    store = _store(tmp_path, clock)
    day_20 = _target(pi_definition, day='20')
    day_21 = _target(pi_definition, day='21')
    store.replace(
        definition=pi_definition,
        target=day_20,
        table=pa.table({'value': pa.array([1], type=pa.int64())}),
    )
    store.replace(
        definition=pi_definition,
        target=day_21,
        table=pa.table({'value': pa.array([1.0], type=pa.float64())}),
    )

    with pytest.raises(ParquetSchemaError):
        store.scan(
            definition=pi_definition,
            targets=(day_20, day_21),
            columns=('value',),
        )


def test_confirmed_artifact_invalid_schema_is_classified_as_corruption(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, clock)
    target = _target(pi_definition)
    store.replace(
        definition=pi_definition,
        target=target,
        table=pa.table({'value': pa.array([1], type=pa.int64())}),
    )

    def reject_schema(_schema: pa.Schema) -> None:
        raise ParquetSchemaError('controlled invalid physical schema')

    monkeypatch.setattr(publication_module, '_validate_schema', reject_schema)

    with pytest.raises(ParquetCorruptionError, match='schema is invalid') as captured:
        store.read(definition=pi_definition, target=target)

    assert isinstance(captured.value.__cause__, ParquetSchemaError)


def test_scan_type_change_after_inspection_is_classified_as_corruption(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, clock)
    target = _target(pi_definition)
    store.replace(
        definition=pi_definition,
        target=target,
        table=pa.table({'value': pa.array([1], type=pa.int64())}),
    )

    monkeypatch.setattr(
        scan_module.pq,
        'read_table',
        lambda *_args, **_kwargs: pa.table({'value': pa.array([1.0], type=pa.float64())}),
    )

    with pytest.raises(ParquetCorruptionError, match='schema changed while scanning'):
        store.scan(
            definition=pi_definition,
            targets=(target,),
            columns=('value',),
        )


def test_read_schema_returns_physical_schema_without_loading_table(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, clock)
    target = _target(pi_definition)
    table = pa.table(
        {
            'timestamp': timestamp_array('2026-07-21T10:00:00Z'),
            'value': pa.array([1.0]),
        }
    )
    store.replace(definition=pi_definition, target=target, table=table)

    monkeypatch.setattr(
        store,
        'scan',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('scan must not be used')),
    )

    schema = store.read_schema(definition=pi_definition, target=target)

    assert schema.equals(table.schema, check_metadata=True)
