from __future__ import annotations

from copy import deepcopy

import pytest

from ada.alarms.persistence import (
    ENGINE_COMMIT_RECORD_SCHEMA_VERSION,
    GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION,
    JOURNAL_HEAD_SCHEMA_VERSION,
    AlarmPersistenceCorruptionError,
    AlarmPersistenceValidationError,
    EngineCommitMetadata,
    EngineCommitRecord,
    GroupRuntimeSnapshot,
    JournalHead,
    JournalPosition,
    parse_segment_id,
    segment_id_for_evaluated_at,
)
from tests.support import build_record, build_snapshot


def test_segment_id_is_derived_from_frozen_utc_hour() -> None:
    value = segment_id_for_evaluated_at('2026-08-23T20:59:59.999999Z')

    assert value == '2026-08-23T20Z#0000'
    assert parse_segment_id(value) == (2026, 8, 23, 20, 0)


def test_segment_id_rejects_non_utc_timestamp() -> None:
    with pytest.raises(ValueError, match='UTC'):
        segment_id_for_evaluated_at('2026-08-23T16:00:00-04:00')


def test_journal_head_round_trip() -> None:
    position = JournalPosition(segment_id='2026-08-23T20Z#0000', byte_offset=123, commit_id='C1')
    head = JournalHead(durable=position, materialized=position)

    rebuilt = JournalHead.from_document(head.as_document())

    assert rebuilt == head
    assert head.as_document()['journal_head_schema_version'] == JOURNAL_HEAD_SCHEMA_VERSION


def test_journal_head_rejects_materialized_ahead_of_durable() -> None:
    durable = JournalPosition(segment_id='2026-08-23T20Z#0000', byte_offset=100, commit_id='C1')
    materialized = JournalPosition(
        segment_id='2026-08-23T20Z#0000', byte_offset=200, commit_id='C2'
    )

    with pytest.raises(ValueError, match='ahead'):
        JournalHead(durable=durable, materialized=materialized)


def test_group_runtime_snapshot_round_trip_preserves_contract() -> None:
    snapshot = build_snapshot()

    rebuilt = GroupRuntimeSnapshot.from_document(snapshot.as_document())

    assert rebuilt.as_document() == snapshot.as_document()
    assert rebuilt.as_document()['snapshot_schema_version'] == GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION


def test_group_runtime_snapshot_rejects_unknown_top_level_field() -> None:
    payload = build_snapshot().as_document()
    payload['history'] = []

    with pytest.raises(AlarmPersistenceCorruptionError, match='unexpected'):
        GroupRuntimeSnapshot.from_document(payload)


def test_engine_commit_record_hash_is_deterministic() -> None:
    first = build_record()
    second = build_record()

    assert first.record_hash == second.record_hash
    assert first.record_hash.startswith('sha256:')
    assert first.as_document()['record_schema_version'] == ENGINE_COMMIT_RECORD_SCHEMA_VERSION


def test_engine_commit_record_detects_payload_tampering() -> None:
    payload = deepcopy(build_record().as_document())
    payload['commit']['runtime_artifact_version'] = 'tampered'

    with pytest.raises(AlarmPersistenceCorruptionError, match='hash'):
        EngineCommitRecord.from_document(payload)


def test_engine_commit_record_accepts_durable_deactivation_request_collection() -> None:
    commit = build_record().commit
    snapshot = build_snapshot()
    request = {
        'request_id': 'DR1',
        'alarm_key': 'mill/risk',
        'source_management_input_id': 'M1',
        'source_occurrence_id': 'O1',
        'requested_at': '2026-08-24T12:00:00Z',
        'effective_until': '2026-08-24T19:00:00Z',
        'approval_required': True,
    }

    record = EngineCommitRecord.create(
        commit=commit,
        snapshot_after=snapshot,
        records={'deactivation_requests': [request]},
    )
    rebuilt = EngineCommitRecord.from_document(record.as_document())

    assert rebuilt.records == {'deactivation_requests': [request]}
    assert rebuilt.record_hash == record.record_hash


def test_engine_commit_record_rejects_unknown_record_collection() -> None:
    commit = build_record().commit
    snapshot = build_snapshot()

    with pytest.raises(AlarmPersistenceValidationError, match='unsupported'):
        EngineCommitRecord.create(
            commit=commit,
            snapshot_after=snapshot,
            records={'raw_inputs': []},
        )


def test_engine_commit_metadata_requires_unique_affected_alarms() -> None:
    with pytest.raises(ValueError, match='duplicates'):
        EngineCommitMetadata(
            commit_id='C1',
            cycle_id='cycle',
            priority_group='group',
            previous_commit_id=None,
            evaluated_at='2026-08-23T20:00:00Z',
            committed_at='2026-08-23T20:00:00Z',
            alarm_configuration_revision='R1',
            tool_registry_revision='T1',
            runtime_artifact_version='runtime/1',
            affected_alarms=('a', 'a'),
        )
