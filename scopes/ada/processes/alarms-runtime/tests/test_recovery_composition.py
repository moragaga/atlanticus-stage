from datetime import timedelta
from pathlib import Path

import pytest

from ada.alarms.core import (
    AlarmEvaluation,
    AlarmStatus,
    EvidenceSnapshot,
    GroupLifecycleState,
    materialize_group_commit,
    reduce_group_cycle,
)
from ada.alarms.persistence import AlarmPersistence
from ada.processes.alarms_runtime import (
    AlarmRuntimeCompositionError,
    build_alarm_runtime_composition,
)
from atlanticus.runtime import RuntimeConfiguration
from tests.support import (
    NOW,
    active_runtime_state,
    build_context,
    group_state,
    identity,
    materialization,
    plan,
)


def _composition(tmp_path: Path):
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada-alarms-runtime-test',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )
    return build_alarm_runtime_composition(runtime_configuration=configuration)


def test_composition_commits_core_materialization_and_hydrates_same_group_state(
    tmp_path: Path,
) -> None:
    composition = _composition(tmp_path)
    context = build_context(tmp_path)
    state = group_state(active_runtime_state())
    current = materialization(state, runtime_state_updates=(identity(),))

    result = composition.commit_batch(context, (current,))
    recovered = composition.load_group('mill-feed', planned_alarms=(plan(),))

    assert result.record_count == 1
    assert recovered.state == state
    assert recovered.last_commit_id == current.commit.commit_id


def test_recovery_replays_durable_after_image_before_core_hydration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    composition = _composition(tmp_path)
    context = build_context(tmp_path)
    state = group_state(active_runtime_state())
    current = materialization(state, runtime_state_updates=(identity(),))
    persistence: AlarmPersistence = composition.durability.persistence
    original_materialize = persistence._materialize_entry

    def fail_after_durable(entry):
        raise RuntimeError('forced crash after durable publication')

    monkeypatch.setattr(persistence, '_materialize_entry', fail_after_durable)
    with pytest.raises(RuntimeError, match='after durable'):
        composition.commit_batch(context, (current,))
    monkeypatch.setattr(persistence, '_materialize_entry', original_materialize)

    recovery = composition.recover(context)
    recovered = composition.load_group('mill-feed', planned_alarms=(plan(),))

    assert recovery.applied_count == 1
    assert recovered.state == state
    assert recovered.last_commit_id == current.commit.commit_id


def test_second_commit_uses_materialized_snapshot_for_alarm_provenance(tmp_path: Path) -> None:
    composition = _composition(tmp_path)
    context = build_context(tmp_path)
    first_state = group_state(active_runtime_state('risk'), active_runtime_state('impact'))
    first = materialization(
        first_state,
        runtime_state_updates=(identity('risk'), identity('impact')),
        affected_alarms=(identity('risk'), identity('impact')),
    )
    composition.commit_batch(context, (first,))

    second_at = NOW + timedelta(minutes=1)
    second_state = group_state(
        active_runtime_state('risk', at=second_at),
        active_runtime_state('impact'),
    )
    second = materialization(
        second_state,
        at=second_at,
        previous_commit_id=first.commit.commit_id,
        runtime_state_updates=(identity('risk'),),
        affected_alarms=(identity('risk'),),
    )
    composition.commit_batch(context, (second,))

    snapshot = composition.durability.persistence.read_snapshot('mill-feed')
    assert snapshot is not None
    alarms = snapshot.as_document()['alarms']
    assert alarms['mill/risk']['last_commit_id'] == second.commit.commit_id
    assert alarms['mill/impact']['last_commit_id'] == first.commit.commit_id


def test_real_core_cycle_commits_and_hydrates_through_persistence(tmp_path: Path) -> None:
    composition = _composition(tmp_path)
    context = build_context(tmp_path)
    alarm_plan = plan()
    previous = GroupLifecycleState(priority_group='mill-feed')
    evaluation = AlarmEvaluation(
        alarm_identity=alarm_plan.identity,
        status=AlarmStatus.ACTIVE,
        evaluated_at=NOW,
        evidence_snapshot=EvidenceSnapshot(
            contract_key='threshold',
            contract_version='1',
            payload={'value': 10.0},
        ),
    )
    decision = reduce_group_cycle(
        previous,
        cycle_at=NOW,
        planned_alarms=(alarm_plan,),
        evaluations=(evaluation,),
        occurrence_id_factory=lambda _identity, _at: 'O1',
        episode_id_factory=lambda _group, _at: 'E1',
    )
    core_materialization = materialize_group_commit(
        previous,
        decision,
        evaluations=(evaluation,),
        cycle_at=NOW,
        committed_at=NOW + timedelta(seconds=1),
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        runtime_artifact_version='ada-alarms-runtime/0.2.0',
    )

    assert core_materialization is not None
    composition.commit_batch(context, (core_materialization,))
    recovered = composition.load_group('mill-feed', planned_alarms=(alarm_plan,))

    assert recovered.state == core_materialization.state
    assert recovered.last_commit_id == core_materialization.commit.commit_id


def test_load_group_fails_closed_when_durable_history_exists_but_snapshot_is_missing(
    tmp_path: Path,
) -> None:
    composition = _composition(tmp_path)
    context = build_context(tmp_path)
    state = group_state(active_runtime_state())
    current = materialization(state, runtime_state_updates=(identity(),))
    composition.commit_batch(context, (current,))
    persistence = composition.durability.persistence
    snapshot_path = persistence.paths.alarms_root / persistence.paths.group_snapshot_relative(
        'mill-feed'
    )
    snapshot_path.unlink()

    with pytest.raises(AlarmRuntimeCompositionError, match='durable history'):
        composition.load_group('mill-feed', planned_alarms=(plan(),))


def test_load_group_allows_neutral_group_when_durable_history_belongs_to_another_group(
    tmp_path: Path,
) -> None:
    composition = _composition(tmp_path)
    context = build_context(tmp_path)
    state = group_state(active_runtime_state())
    current = materialization(state, runtime_state_updates=(identity(),))
    composition.commit_batch(context, (current,))

    recovered = composition.load_group('other-group', planned_alarms=())

    assert recovered.state.priority_group == 'other-group'
    assert recovered.state.episode is None
    assert recovered.state.alarms == ()
    assert recovered.snapshot is None
