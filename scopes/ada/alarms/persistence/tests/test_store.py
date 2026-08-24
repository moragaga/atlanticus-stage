from __future__ import annotations

from pathlib import Path

import pytest

from ada.alarms.persistence import (
    AlarmPersistence,
    AlarmPersistenceConflictError,
    AlarmRecoveryRequiredError,
)
from tests.support import build_record, mutation_fence


def _authority() -> None:
    return None


def test_commit_batch_persists_wal_head_and_snapshot(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    record = build_record()

    result = persistence.commit_batch(
        [record], assert_authority=_authority, fenced_mutation=mutation_fence
    )

    assert result.record_count == 1
    assert result.durable == result.materialized
    assert result.bytes_appended > 0
    assert persistence.read_head().durable == result.durable
    assert persistence.read_snapshot('crusher_pressure') == record.snapshot_after
    durable_records = persistence.read_durable_records()
    assert [entry.record.commit.commit_id for entry in durable_records] == ['C1']


def test_batch_uses_one_segment_and_deterministic_group_order(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    second = build_record(
        commit_id='C-b',
        priority_group='z_group',
        alarm_key='z_alarm',
        cycle_id='20260823T200000000000Z',
    )
    first = build_record(
        commit_id='C-a',
        priority_group='a_group',
        alarm_key='a_alarm',
        cycle_id='20260823T200000000000Z',
    )

    persistence.commit_batch(
        [second, first], assert_authority=_authority, fenced_mutation=mutation_fence
    )

    records = persistence.read_durable_records()
    assert [entry.record.commit.priority_group for entry in records] == ['a_group', 'z_group']
    assert len({entry.end.segment_id for entry in records}) == 1


def test_commit_batch_rejects_empty_input_without_creating_journal(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)

    with pytest.raises(ValueError, match='must not be empty'):
        persistence.commit_batch([], assert_authority=_authority, fenced_mutation=mutation_fence)

    assert not persistence.paths.journal_open_root.exists()


def test_commit_batch_requires_physical_mutation_fence(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)

    with pytest.raises(TypeError, match='fenced_mutation must be callable'):
        persistence.commit_batch(
            [build_record()],
            assert_authority=_authority,
            fenced_mutation=None,  # type: ignore[arg-type]
        )

    assert not persistence.paths.journal_open_root.exists()


def test_commit_batch_rejects_duplicate_priority_group_in_same_cycle(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    first = build_record(commit_id='C1')
    second = build_record(commit_id='C2')

    with pytest.raises(ValueError, match='at most one commit'):
        persistence.commit_batch(
            [first, second], assert_authority=_authority, fenced_mutation=mutation_fence
        )


def test_commit_requires_current_previous_commit_id(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    persistence.commit_batch(
        [build_record(commit_id='C1')], assert_authority=_authority, fenced_mutation=mutation_fence
    )

    stale = build_record(commit_id='C2', previous_commit_id='WRONG')

    with pytest.raises(AlarmPersistenceConflictError, match='previous_commit_id'):
        persistence.commit_batch(
            [stale], assert_authority=_authority, fenced_mutation=mutation_fence
        )


def test_commit_rejects_new_work_when_recovery_is_pending(tmp_path: Path, monkeypatch) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    record = build_record(commit_id='C1')
    original = persistence._materialize_entry

    def fail_materialization(entry):
        raise RuntimeError('forced crash')

    monkeypatch.setattr(persistence, '_materialize_entry', fail_materialization)
    with pytest.raises(RuntimeError, match='forced crash'):
        persistence.commit_batch(
            [record], assert_authority=_authority, fenced_mutation=mutation_fence
        )
    monkeypatch.setattr(persistence, '_materialize_entry', original)

    with pytest.raises(AlarmRecoveryRequiredError, match='recovered'):
        persistence.commit_batch(
            [build_record(commit_id='C2', previous_commit_id='C1')],
            assert_authority=_authority,
            fenced_mutation=mutation_fence,
        )


def test_list_snapshots_returns_current_group_states(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    records = [
        build_record(commit_id='A1', priority_group='a', alarm_key='alarm_a'),
        build_record(commit_id='B1', priority_group='b', alarm_key='alarm_b'),
    ]
    persistence.commit_batch(records, assert_authority=_authority, fenced_mutation=mutation_fence)

    snapshots = persistence.list_snapshots()

    assert [snapshot.priority_group for snapshot in snapshots] == ['a', 'b']


def test_alarm_persistence_explicitly_uses_unbounded_state_documents_by_default(
    tmp_path: Path,
) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    large_error_key = 'x' * (1024 * 1024 + 128)
    record = build_record(error_key=large_error_key)

    persistence.commit_batch([record], assert_authority=_authority, fenced_mutation=mutation_fence)

    assert persistence.read_snapshot('crusher_pressure') is not None
