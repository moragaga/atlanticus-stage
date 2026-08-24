from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from ada.alarms.persistence import AlarmPersistence, AlarmPersistenceCorruptionError
from tests.support import build_record, mutation_fence


def _authority() -> None:
    return None


class _MutationFenceSequence:
    def __init__(self, *, fail_on: int) -> None:
        self.calls = 0
        self.fail_on = fail_on

    @contextmanager
    def __call__(self) -> Iterator[None]:
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError('lease lost')
        yield


def test_crash_after_wal_before_durable_head_discards_tail(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    fence = _MutationFenceSequence(fail_on=2)

    with pytest.raises(RuntimeError, match='lease lost'):
        persistence.commit_batch(
            [build_record()],
            assert_authority=_authority,
            fenced_mutation=fence,
        )

    assert persistence.read_head().durable is None
    open_files = list(persistence.paths.journal_open_root.rglob('*.jsonl'))
    assert len(open_files) == 1
    assert open_files[0].stat().st_size > 0

    result = persistence.recover(assert_authority=_authority, fenced_mutation=mutation_fence)

    assert result.discarded_tail_bytes > 0
    assert persistence.read_head().durable is None
    assert not list(persistence.paths.journal_open_root.rglob('*.jsonl'))


def test_crash_after_durable_before_snapshot_replays_exact_after_image(
    tmp_path: Path, monkeypatch
) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    record = build_record()
    original = persistence._materialize_entry

    def fail(entry):
        raise RuntimeError('crash after durable')

    monkeypatch.setattr(persistence, '_materialize_entry', fail)
    with pytest.raises(RuntimeError, match='after durable'):
        persistence.commit_batch(
            [record], assert_authority=_authority, fenced_mutation=mutation_fence
        )
    monkeypatch.setattr(persistence, '_materialize_entry', original)

    head = persistence.read_head()
    assert head.durable is not None
    assert head.materialized is None
    assert persistence.read_snapshot('crusher_pressure') is None

    result = persistence.recover(assert_authority=_authority, fenced_mutation=mutation_fence)

    assert result.applied_count == 1
    assert result.skipped_count == 0
    assert persistence.read_snapshot('crusher_pressure') == record.snapshot_after
    assert persistence.read_head().aligned


def test_crash_during_multiple_snapshot_replaces_is_idempotently_recovered(
    tmp_path: Path, monkeypatch
) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    first = build_record(commit_id='A1', priority_group='a', alarm_key='alarm_a')
    second = build_record(commit_id='B1', priority_group='b', alarm_key='alarm_b')
    original_replace = persistence._state.replace
    group_writes = 0

    def fail_second_group(relative_path, value):
        nonlocal group_writes
        if str(relative_path).startswith('runtime/state/groups/'):
            group_writes += 1
            if group_writes == 2:
                raise OSError('forced snapshot failure')
        return original_replace(relative_path, value)

    monkeypatch.setattr(persistence._state, 'replace', fail_second_group)
    with pytest.raises(OSError, match='forced snapshot failure'):
        persistence.commit_batch(
            [second, first], assert_authority=_authority, fenced_mutation=mutation_fence
        )
    monkeypatch.setattr(persistence._state, 'replace', original_replace)

    assert persistence.read_snapshot('a') == first.snapshot_after
    assert persistence.read_snapshot('b') is None

    result = persistence.recover(assert_authority=_authority, fenced_mutation=mutation_fence)

    assert result.applied_count == 1
    assert result.skipped_count == 1
    assert persistence.read_snapshot('a') == first.snapshot_after
    assert persistence.read_snapshot('b') == second.snapshot_after
    assert persistence.read_head().aligned


def test_crash_after_snapshots_before_materialized_head_skips_already_applied_state(
    tmp_path: Path, monkeypatch
) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    record = build_record()
    original_replace_head = persistence._replace_head
    head_writes = 0

    def fail_second_head(head):
        nonlocal head_writes
        head_writes += 1
        if head_writes == 2:
            raise RuntimeError('crash before materialized')
        return original_replace_head(head)

    monkeypatch.setattr(persistence, '_replace_head', fail_second_head)
    with pytest.raises(RuntimeError, match='before materialized'):
        persistence.commit_batch(
            [record], assert_authority=_authority, fenced_mutation=mutation_fence
        )
    monkeypatch.setattr(persistence, '_replace_head', original_replace_head)

    assert persistence.read_snapshot('crusher_pressure') == record.snapshot_after
    assert not persistence.read_head().aligned

    result = persistence.recover(assert_authority=_authority, fenced_mutation=mutation_fence)

    assert result.applied_count == 0
    assert result.skipped_count == 1
    assert persistence.read_head().aligned


def test_non_durable_tail_is_truncated_without_replaying_it(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    result = persistence.commit_batch(
        [build_record()], assert_authority=_authority, fenced_mutation=mutation_fence
    )
    path = persistence.paths.journal_segment_path(result.durable.segment_id, sealed=False)
    with path.open('ab') as file_handle:
        file_handle.write(b'{"unconfirmed":true}\n')

    before = path.stat().st_size
    recovery = persistence.recover(assert_authority=_authority, fenced_mutation=mutation_fence)

    assert recovery.discarded_tail_bytes == before - result.durable.byte_offset
    assert path.stat().st_size == result.durable.byte_offset
    assert persistence.read_durable_records()[-1].record.commit.commit_id == 'C1'


def test_corruption_inside_durable_region_stops_normal_recovery(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    result = persistence.commit_batch(
        [build_record()], assert_authority=_authority, fenced_mutation=mutation_fence
    )
    path = persistence.paths.journal_segment_path(result.durable.segment_id, sealed=False)
    content = bytearray(path.read_bytes())
    content[10] = ord('!')
    path.write_bytes(content)

    with pytest.raises(AlarmPersistenceCorruptionError):
        persistence.recover(assert_authority=_authority, fenced_mutation=mutation_fence)


def test_corrupt_journal_head_fails_closed(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    persistence.commit_batch(
        [build_record()], assert_authority=_authority, fenced_mutation=mutation_fence
    )
    head_path = persistence.paths.alarms_root / persistence.paths.journal_head_relative
    head_path.write_bytes(b'{bad json\n')

    with pytest.raises(AlarmPersistenceCorruptionError, match='journal head'):
        persistence.read_head()


def test_snapshots_without_durable_head_are_corruption(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    snapshot = build_record().snapshot_after
    persistence._state.replace(
        persistence.paths.group_snapshot_relative('crusher_pressure'), snapshot.as_document()
    )

    with pytest.raises(AlarmPersistenceCorruptionError, match='without a durable'):
        persistence.recover(assert_authority=_authority, fenced_mutation=mutation_fence)
