from dataclasses import replace

from ada.alarms.core import DeactivationRequestRecord, EngineCommitRecords
from ada.alarms.persistence import EngineCommitRecord
from ada.processes.alarms_runtime import compose_engine_commit_record
from tests.support import active_runtime_state, group_state, identity, materialization


def test_core_materialization_composes_engine_commit_record_v1() -> None:
    state = group_state(active_runtime_state())
    core_materialization = materialization(state, runtime_state_updates=(identity(),))

    record = compose_engine_commit_record(core_materialization, previous_snapshot=None)

    assert isinstance(record, EngineCommitRecord)
    assert record.commit.commit_id == core_materialization.commit.commit_id
    assert record.commit.cycle_id == core_materialization.commit.cycle_id
    assert record.commit.affected_alarms == ('mill/risk',)
    assert record.snapshot_after.last_commit_id == core_materialization.commit.commit_id
    assert record.record_hash.startswith('sha256:')
    assert len(record.record_hash) == 71


def test_record_payload_uses_core_records_without_parallel_serialization() -> None:
    state = group_state(active_runtime_state())
    core_materialization = materialization(state, runtime_state_updates=(identity(),))

    record = compose_engine_commit_record(core_materialization, previous_snapshot=None)

    assert record.records == core_materialization.records.as_document()


def test_deactivation_request_record_crosses_runtime_persistence_boundary_unchanged() -> None:
    state = group_state(active_runtime_state())
    base = materialization(state, runtime_state_updates=(identity(),))
    request = DeactivationRequestRecord(
        request_id='DR1',
        alarm_identity=identity(),
        source_management_input_id='M1',
        source_occurrence_id='O-risk',
        requested_at=base.commit.evaluated_at,
        effective_until=base.commit.evaluated_at.replace(hour=19),
        approval_required=True,
    )
    core_materialization = replace(
        base,
        commit=replace(base.commit, deactivation_request_ids=('DR1',)),
        records=EngineCommitRecords(deactivation_requests=(request,)),
    )

    record = compose_engine_commit_record(core_materialization, previous_snapshot=None)

    assert record.records == {
        'deactivation_requests': [request.as_document()],
    }
