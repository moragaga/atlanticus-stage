from pathlib import Path

import pytest

from ada.alarms.persistence import AlarmPersistence, AlarmPersistenceCorruptionError
from tests.support import build_record, mutation_fence


def _authority() -> None:
    return None


def test_each_wal_record_is_complete_newline_terminated_json(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    result = persistence.commit_batch(
        [build_record()], assert_authority=_authority, fenced_mutation=mutation_fence
    )
    path = persistence.paths.journal_segment_path(result.durable.segment_id, sealed=False)

    content = path.read_bytes()

    assert content.endswith(b'\n')
    assert content.count(b'\n') == 1


def test_reader_can_resume_from_a_confirmed_record_boundary(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    first = build_record(commit_id='A1', priority_group='a', alarm_key='alarm_a')
    second = build_record(commit_id='B1', priority_group='b', alarm_key='alarm_b')
    persistence.commit_batch(
        [second, first], assert_authority=_authority, fenced_mutation=mutation_fence
    )
    all_records = persistence.read_durable_records()

    remaining = persistence.read_durable_records(after=all_records[0].end)

    assert [entry.record.commit.commit_id for entry in remaining] == ['B1']


def test_reader_rejects_non_record_boundary(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    persistence.commit_batch(
        [build_record()], assert_authority=_authority, fenced_mutation=mutation_fence
    )
    head = persistence.read_head()
    assert head.durable is not None
    invalid = type(head.durable)(
        segment_id=head.durable.segment_id,
        byte_offset=1,
        commit_id='not-a-boundary',
    )

    with pytest.raises(AlarmPersistenceCorruptionError):
        persistence.read_durable_records(after=invalid)
