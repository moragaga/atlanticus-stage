from pathlib import Path

from ada.alarms.persistence import AlarmPersistence
from tests.support import build_record, mutation_fence


def _authority() -> None:
    return None


def test_hour_change_seals_previous_reconciled_segment(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    first = build_record(
        commit_id='C1',
        evaluated_at='2026-08-23T20:59:59Z',
        cycle_id='20260823T205959000000Z',
    )
    first_result = persistence.commit_batch(
        [first], assert_authority=_authority, fenced_mutation=mutation_fence
    )
    second = build_record(
        commit_id='C2',
        previous_commit_id='C1',
        evaluated_at='2026-08-23T21:00:01Z',
        cycle_id='20260823T210001000000Z',
    )

    second_result = persistence.commit_batch(
        [second], assert_authority=_authority, fenced_mutation=mutation_fence
    )

    assert second_result.sealed_segment_count == 1
    assert not persistence.paths.journal_segment_path(
        first_result.durable.segment_id, sealed=False
    ).exists()
    assert persistence.paths.journal_segment_path(
        first_result.durable.segment_id, sealed=True
    ).exists()
    assert persistence.paths.journal_segment_path(
        second_result.durable.segment_id, sealed=False
    ).exists()


def test_durable_reader_crosses_sealed_and_open_segments(tmp_path: Path) -> None:
    persistence = AlarmPersistence(shared_volume_path=tmp_path)
    first = build_record(
        commit_id='C1',
        evaluated_at='2026-08-23T20:59:59Z',
        cycle_id='20260823T205959000000Z',
    )
    persistence.commit_batch([first], assert_authority=_authority, fenced_mutation=mutation_fence)
    second = build_record(
        commit_id='C2',
        previous_commit_id='C1',
        evaluated_at='2026-08-23T21:00:01Z',
        cycle_id='20260823T210001000000Z',
    )
    persistence.commit_batch([second], assert_authority=_authority, fenced_mutation=mutation_fence)

    records = persistence.read_durable_records()

    assert [entry.record.commit.commit_id for entry in records] == ['C1', 'C2']
