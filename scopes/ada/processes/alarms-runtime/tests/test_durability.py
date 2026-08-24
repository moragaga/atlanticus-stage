from pathlib import Path

import pytest

from ada.alarms.persistence import AlarmPersistence
from ada.processes.alarms_runtime.durability import AlarmRuntimeDurability
from atlanticus.runtime import JobRuntimeContext, RuntimeContractError
from tests.support import build_context, build_record


def test_recovery_runs_under_runtime_authority(tmp_path: Path) -> None:
    context = build_context(tmp_path)
    durability = AlarmRuntimeDurability(persistence=AlarmPersistence(shared_volume_path=tmp_path))

    result = durability.recover(context)

    assert result.applied_count == 0
    assert result.discarded_tail_bytes == 0
    assert context.get_execution_fact('alarm_recovery_applied_count') == 0
    assert context.get_execution_fact('alarm_recovery_discarded_tail_bytes') == 0


def test_commit_batch_uses_runtime_fencing_and_records_summary(tmp_path: Path) -> None:
    context = build_context(tmp_path)
    durability = AlarmRuntimeDurability(persistence=AlarmPersistence(shared_volume_path=tmp_path))

    result = durability.commit_batch(context, [build_record()])

    assert result.record_count == 1
    assert result.durable == result.materialized
    assert context.get_iteration_fact('alarm_commit_record_count') == 1
    assert context.get_execution_fact('alarm_commits_confirmed') == 1
    assert durability.persistence.read_snapshot('crusher_pressure').last_commit_id == 'C1'


def test_commit_fails_closed_without_bound_runtime_authority(tmp_path: Path) -> None:
    bound = build_context(tmp_path)
    context = JobRuntimeContext.create(
        definition=bound.definition,
        configuration=bound.configuration,
        run_id='33333333-3333-3333-3333-333333333333',
        correlation_id='44444444-4444-4444-4444-444444444444',
    )
    durability = AlarmRuntimeDurability(persistence=AlarmPersistence(shared_volume_path=tmp_path))

    with pytest.raises(RuntimeContractError, match='authority is not available'):
        durability.commit_batch(context, [build_record()])


def test_durability_requires_alarm_persistence() -> None:
    with pytest.raises(TypeError, match='persistence'):
        AlarmRuntimeDurability(persistence=object())  # type: ignore[arg-type]
