from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import atlanticus.datasets.parquet.store as store_module
from atlanticus.datasets import DatasetDefinition, PublicationStatus
from atlanticus.datasets.parquet import (
    ColumnFilter,
    FilterOperator,
    ParquetCorruptionError,
    ParquetDatasetStore,
    ParquetPart,
    ParquetReadError,
    ParquetSchemaError,
    ParquetValidationError,
    ParquetWriteError,
)


def _target(definition: DatasetDefinition):
    return definition.resolve_target(
        materialization='operational-day',
        partition={'year': '2026', 'month': '07', 'day': '21'},
    )


def _part(
    definition: DatasetDefinition,
    target,
    *,
    shift_id: str,
    tonnage: tuple[float, ...],
) -> ParquetPart:
    return ParquetPart(
        key=definition.resolve_part(target=target, value=shift_id),
        table=pa.table(
            {
                'shift_id': pa.array([int(shift_id)] * len(tonnage), type=pa.int64()),
                'equipment': pa.array([f'TRUCK-{index + 1}' for index in range(len(tonnage))]),
                'tonnage': pa.array(tonnage, type=pa.float64()),
            }
        ),
    )

def test_poc_dispatch_uses_flat_parts_manifest_and_shift_pruning(
    tmp_path: Path,
    clock: datetime,
    dispatch_definition: DatasetDefinition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(dispatch_definition)
    shift_001 = _part(
        dispatch_definition,
        target,
        shift_id='26199001',
        tonnage=(100.0, 110.0),
    )
    shift_002 = _part(
        dispatch_definition,
        target,
        shift_id='26199002',
        tonnage=(120.0,),
    )

    publication = store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(shift_001, shift_002),
    )
    target_path = store.path_for(definition=dispatch_definition, target=target)
    manifest = json.loads((target_path / 'current.json').read_text(encoding='utf-8'))
    parquet_files = tuple(target_path.glob('*.parquet'))

    assert publication.status is PublicationStatus.COMMITTED
    assert publication.artifact_count == 2
    assert len(parquet_files) == 2
    assert not (target_path / 'parts').exists()
    assert all('/' not in part['path'] for part in manifest['parts'])
    assert all('--' in part['path'] for part in manifest['parts'])

    expected_part = next(
        part['path'] for part in manifest['parts'] if part['value'] == '26199002'
    )
    inspected_paths: list[str] = []
    original_signature = store_module._file_signature

    def track_signature(path: Path) -> str:
        inspected_paths.append(path.name)
        return original_signature(path)

    monkeypatch.setattr(store_module, '_file_signature', track_signature)

    selected = store.scan(
        definition=dispatch_definition,
        targets=(target,),
        columns=('equipment', 'tonnage'),
        filters=(
            ColumnFilter(
                column='shift_id',
                operator=FilterOperator.IN,
                value=(26199002,),
            ),
        ),
    )

    assert selected.table.to_pydict() == {
        'equipment': ['TRUCK-1'],
        'tonnage': [120.0],
    }
    assert selected.artifact_count == 1
    assert inspected_paths == [expected_part]

def test_signature_read_failure_is_exposed_as_parquet_read_error(
    tmp_path: Path,
    clock: datetime,
    dispatch_definition: DatasetDefinition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(dispatch_definition)
    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(
            _part(
                dispatch_definition,
                target,
                shift_id='26199001',
                tonnage=(100.0,),
            ),
        ),
    )

    def fail_signature(_path: Path) -> str:
        raise PermissionError('controlled signature read failure')

    monkeypatch.setattr(store_module, '_file_signature', fail_signature)

    with pytest.raises(ParquetReadError, match='could not read parquet artifact'):
        store.read(definition=dispatch_definition, target=target)

def test_part_update_preserves_unmentioned_part_and_ignores_orphans(
    tmp_path: Path,
    clock: datetime,
    dispatch_definition: DatasetDefinition,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(dispatch_definition)
    part_001 = _part(
        dispatch_definition,
        target,
        shift_id='26199001',
        tonnage=(100.0,),
    )
    part_002 = _part(
        dispatch_definition,
        target,
        shift_id='26199002',
        tonnage=(200.0,),
    )
    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(part_001, part_002),
    )
    target_path = store.path_for(definition=dispatch_definition, target=target)
    first_manifest = json.loads((target_path / 'current.json').read_text(encoding='utf-8'))
    old_001_path = next(
        target_path / part['path']
        for part in first_manifest['parts']
        if part['value'] == '26199001'
    )

    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(
            _part(
                dispatch_definition,
                target,
                shift_id='26199001',
                tonnage=(150.0,),
            ),
        ),
    )
    result = store.read(definition=dispatch_definition, target=target)

    assert sorted(result.table['tonnage'].to_pylist()) == [150.0, 200.0]
    assert old_001_path.exists()
    assert old_001_path.name not in {
        part['path']
        for part in json.loads((target_path / 'current.json').read_text(encoding='utf-8'))['parts']
    }

def test_incoming_schema_adds_and_removes_columns_without_rewriting_old_parts(
    tmp_path: Path,
    clock: datetime,
    dispatch_definition: DatasetDefinition,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(dispatch_definition)
    old_schema_parts = tuple(
        ParquetPart(
            key=dispatch_definition.resolve_part(target=target, value=shift_id),
            table=pa.table(
                {
                    'shift_id': pa.array([int(shift_id)], type=pa.int64()),
                    'tonnage': pa.array([tonnage]),
                    'retired': pa.array([1.0]),
                }
            ),
        )
        for shift_id, tonnage in (('26199001', 100.0), ('26199002', 200.0))
    )
    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=old_schema_parts,
    )
    replacement = ParquetPart(
        key=dispatch_definition.resolve_part(target=target, value='26199001'),
        table=pa.table(
            {
                'shift_id': pa.array([26199001], type=pa.int64()),
                'tonnage': pa.array([150.0]),
                'new_value': pa.array([15.0]),
            }
        ),
    )

    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(replacement,),
    )
    table = store.read(definition=dispatch_definition, target=target).table

    assert table.column_names == ['shift_id', 'tonnage', 'new_value']
    assert sorted(table['new_value'].to_pylist(), key=lambda value: value is None) == [15.0, None]
    assert 'retired' not in table.column_names

def test_empty_part_skips_the_entire_target_and_preserves_current_manifest(
    tmp_path: Path,
    clock: datetime,
    dispatch_definition: DatasetDefinition,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(dispatch_definition)
    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(
            _part(
                dispatch_definition,
                target,
                shift_id='26199001',
                tonnage=(100.0,),
            ),
        ),
    )
    target_path = store.path_for(definition=dispatch_definition, target=target)
    before = (target_path / 'current.json').read_bytes()
    empty = ParquetPart(
        key=dispatch_definition.resolve_part(target=target, value='26199002'),
        table=pa.table(
            {
                'shift_id': pa.array([], type=pa.int64()),
                'equipment': pa.array([], type=pa.string()),
                'tonnage': pa.array([], type=pa.float64()),
            }
        ),
    )

    result = store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(empty,),
    )

    assert result.status is PublicationStatus.SKIPPED
    assert (target_path / 'current.json').read_bytes() == before

def test_empty_new_file_set_does_not_create_a_target(
    tmp_path: Path,
    clock: datetime,
    dispatch_definition: DatasetDefinition,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(dispatch_definition)
    empty = ParquetPart(
        key=dispatch_definition.resolve_part(target=target, value='26199001'),
        table=pa.table(
            {
                'shift_id': pa.array([], type=pa.int64()),
                'equipment': pa.array([], type=pa.string()),
                'tonnage': pa.array([], type=pa.float64()),
            }
        ),
    )

    result = store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(empty,),
    )

    assert result.status is PublicationStatus.SKIPPED
    assert not store.path_for(definition=dispatch_definition, target=target).exists()

def test_parts_are_removed_only_when_their_keys_are_explicit(
    tmp_path: Path,
    clock: datetime,
    dispatch_definition: DatasetDefinition,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(dispatch_definition)
    part_001 = _part(
        dispatch_definition,
        target,
        shift_id='26199001',
        tonnage=(100.0,),
    )
    part_002 = _part(
        dispatch_definition,
        target,
        shift_id='26199002',
        tonnage=(200.0,),
    )
    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(part_001, part_002),
    )

    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        remove_parts=(part_001.key,),
    )

    assert store.read(definition=dispatch_definition, target=target).table[
        'shift_id'
    ].to_pylist() == [26199002]
    with pytest.raises(ParquetValidationError):
        store.publish_parts(
            definition=dispatch_definition,
            target=target,
            remove_parts=(part_002.key,),
        )
    assert store.read(definition=dispatch_definition, target=target).table[
        'shift_id'
    ].to_pylist() == [26199002]

def test_failed_manifest_commit_preserves_previous_part_set(
    tmp_path: Path,
    clock: datetime,
    dispatch_definition: DatasetDefinition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(dispatch_definition)
    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(
            _part(
                dispatch_definition,
                target,
                shift_id='26199001',
                tonnage=(100.0,),
            ),
        ),
    )
    original_replace = store_module.os.replace

    def fail_manifest(source: Path, destination: Path) -> None:
        if Path(destination).name == 'current.json':
            raise OSError('controlled manifest failure')
        original_replace(source, destination)

    monkeypatch.setattr(store_module.os, 'replace', fail_manifest)

    with pytest.raises(ParquetWriteError):
        store.publish_parts(
            definition=dispatch_definition,
            target=target,
            incoming_parts=(
                _part(
                    dispatch_definition,
                    target,
                    shift_id='26199001',
                    tonnage=(999.0,),
                ),
            ),
        )

    assert store.read(definition=dispatch_definition, target=target).table[
        'tonnage'
    ].to_pylist() == [100.0]

def test_missing_referenced_part_is_corruption_not_partial_data(
    tmp_path: Path,
    clock: datetime,
    dispatch_definition: DatasetDefinition,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(dispatch_definition)
    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(
            _part(
                dispatch_definition,
                target,
                shift_id='26199001',
                tonnage=(100.0,),
            ),
        ),
    )
    target_path = store.path_for(definition=dispatch_definition, target=target)
    manifest = json.loads((target_path / 'current.json').read_text(encoding='utf-8'))
    (target_path / manifest['parts'][0]['path']).unlink()

    with pytest.raises(ParquetCorruptionError):
        store.read(definition=dispatch_definition, target=target)

def test_modified_referenced_part_is_rejected_by_content_signature(
    tmp_path: Path,
    clock: datetime,
    dispatch_definition: DatasetDefinition,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(dispatch_definition)
    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(
            _part(
                dispatch_definition,
                target,
                shift_id='26199001',
                tonnage=(100.0,),
            ),
        ),
    )
    target_path = store.path_for(definition=dispatch_definition, target=target)
    manifest_path = target_path / 'current.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    part_path = target_path / manifest['parts'][0]['path']
    pq.write_table(
        _part(
            dispatch_definition,
            target,
            shift_id='26199001',
            tonnage=(999.0,),
        ).table,
        part_path,
        compression='zstd',
        compression_level=3,
        use_dictionary=True,
        write_statistics=True,
        row_group_size=131_072,
    )
    manifest['parts'][0]['size_bytes'] = part_path.stat().st_size
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )

    with pytest.raises(ParquetCorruptionError, match='signature'):
        store.read(definition=dispatch_definition, target=target)

def test_type_change_requires_republishing_or_removing_incompatible_parts(
    tmp_path: Path,
    clock: datetime,
    dispatch_definition: DatasetDefinition,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(dispatch_definition)
    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(
            _part(
                dispatch_definition,
                target,
                shift_id='26199001',
                tonnage=(100.0,),
            ),
            _part(
                dispatch_definition,
                target,
                shift_id='26199002',
                tonnage=(200.0,),
            ),
        ),
    )
    changed_type = ParquetPart(
        key=dispatch_definition.resolve_part(target=target, value='26199001'),
        table=pa.table(
            {
                'shift_id': pa.array([26199001], type=pa.int64()),
                'equipment': ['TRUCK-1'],
                'tonnage': pa.array([150], type=pa.int64()),
            }
        ),
    )

    with pytest.raises(ParquetSchemaError):
        store.publish_parts(
            definition=dispatch_definition,
            target=target,
            incoming_parts=(changed_type,),
        )

    assert sorted(
        store.read(definition=dispatch_definition, target=target).table['tonnage'].to_pylist()
    ) == [100.0, 200.0]


def test_confirmed_part_schema_mismatch_is_classified_as_corruption(
    tmp_path: Path,
    clock: datetime,
    dispatch_definition: DatasetDefinition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParquetDatasetStore(root=tmp_path, clock=lambda: clock)
    target = _target(dispatch_definition)
    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(
            _part(
                dispatch_definition,
                target,
                shift_id='26199001',
                tonnage=(100.0,),
            ),
        ),
    )

    def reject_physical_schema(**_kwargs: object) -> None:
        raise ParquetSchemaError('controlled physical schema mismatch')

    monkeypatch.setattr(store, '_validate_physical_schema', reject_physical_schema)

    with pytest.raises(ParquetCorruptionError, match='current manifest') as captured:
        store.read(definition=dispatch_definition, target=target)

    assert isinstance(captured.value.__cause__, ParquetSchemaError)
