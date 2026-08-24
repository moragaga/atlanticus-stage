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
