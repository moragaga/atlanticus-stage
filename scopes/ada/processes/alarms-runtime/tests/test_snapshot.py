from dataclasses import replace
from datetime import timedelta

import pytest

from ada.alarms.core import (
    AlarmIdentity,
    AlarmRuntimeState,
    AlarmStatus,
    DeactivationEffect,
    GroupCommitMaterialization,
    GroupLifecycleState,
    RuntimeEvaluationState,
)
from ada.alarms.persistence import GroupRuntimeSnapshot
from ada.processes.alarms_runtime import (
    AlarmRuntimeCompositionError,
    decode_group_runtime_snapshot,
    encode_group_runtime_snapshot,
)
from tests.support import (
    NOW,
    active_runtime_state,
    error_runtime_state,
    group_state,
    identity,
    materialization,
    plan,
)


def test_active_group_round_trip_uses_compact_last_evaluation() -> None:
    state = group_state(active_runtime_state())
    current = materialization(state, runtime_state_updates=(identity(),))

    snapshot = encode_group_runtime_snapshot(
        state,
        commit=current.commit,
        previous_snapshot=None,
    )
    document = snapshot.as_document()
    last_evaluation = document['alarms']['mill/risk']['occurrence']['last_evaluation']

    assert last_evaluation == {
        'status': 'ACTIVE',
        'evaluated_at': '2026-08-24T12:00:00Z',
    }
    assert 'evidence_snapshot' not in last_evaluation
    assert decode_group_runtime_snapshot(snapshot, planned_alarms=(plan(),)) == state


def test_error_technical_hold_round_trip_keeps_only_error_key() -> None:
    state = group_state(error_runtime_state(), started_at=NOW - timedelta(minutes=1))
    current = materialization(state, runtime_state_updates=(identity(),))

    snapshot = encode_group_runtime_snapshot(
        state,
        commit=current.commit,
        previous_snapshot=None,
    )
    hydrated = decode_group_runtime_snapshot(snapshot, planned_alarms=(plan(),))
    runtime = hydrated.get(identity())

    assert runtime is not None
    assert runtime.last_evaluation == RuntimeEvaluationState(
        status=AlarmStatus.ERROR,
        evaluated_at=NOW,
        error_key='pi-quality',
    )
    assert runtime.technical_hold is not None
    assert runtime.next_evidence_due_at is None


def test_alarm_commit_provenance_only_advances_for_runtime_state_updates() -> None:
    first_state = group_state(active_runtime_state('risk'), active_runtime_state('impact'))
    first = materialization(
        first_state,
        runtime_state_updates=(identity('risk'), identity('impact')),
        affected_alarms=(identity('risk'), identity('impact')),
    )
    first_snapshot = encode_group_runtime_snapshot(
        first_state,
        commit=first.commit,
        previous_snapshot=None,
    )
    second_at = NOW + timedelta(minutes=1)
    risk = active_runtime_state('risk', at=second_at)
    impact = active_runtime_state('impact')
    second_state = group_state(risk, impact)
    second = materialization(
        second_state,
        at=second_at,
        previous_commit_id=first.commit.commit_id,
        runtime_state_updates=(identity('risk'),),
        affected_alarms=(identity('risk'),),
        alarm_configuration_revision='R43',
        tool_registry_revision='T19',
    )

    second_snapshot = encode_group_runtime_snapshot(
        second_state,
        commit=second.commit,
        previous_snapshot=first_snapshot,
    )
    alarms = second_snapshot.as_document()['alarms']

    assert second_snapshot.last_commit_id == second.commit.commit_id
    assert alarms['mill/risk']['last_commit_id'] == second.commit.commit_id
    assert alarms['mill/impact']['last_commit_id'] == first.commit.commit_id


def test_receipt_only_commit_advances_group_head_without_rewriting_state_provenance() -> None:
    state = group_state(active_runtime_state())
    first = materialization(state, runtime_state_updates=(identity(),))
    first_snapshot = encode_group_runtime_snapshot(
        state,
        commit=first.commit,
        previous_snapshot=None,
    )
    second_at = NOW + timedelta(minutes=1)
    second = materialization(
        state,
        at=second_at,
        previous_commit_id=first.commit.commit_id,
        affected_alarms=(identity(),),
        alarm_configuration_revision='R99',
        tool_registry_revision='T99',
        receipt_input_id='M1',
    )

    second_snapshot = encode_group_runtime_snapshot(
        state,
        commit=second.commit,
        previous_snapshot=first_snapshot,
    )
    document = second_snapshot.as_document()

    assert second_snapshot.last_commit_id == second.commit.commit_id
    assert document['alarms']['mill/risk']['last_commit_id'] == first.commit.commit_id
    assert document['state_basis'] == {
        'alarm_configuration_revision': 'R42',
        'tool_registry_revision': 'T18',
    }


def test_decode_fails_closed_for_alarm_key_missing_from_current_configuration() -> None:
    state = group_state(active_runtime_state())
    current = materialization(state, runtime_state_updates=(identity(),))
    snapshot = encode_group_runtime_snapshot(
        state,
        commit=current.commit,
        previous_snapshot=None,
    )

    with pytest.raises(AlarmRuntimeCompositionError, match='current configuration'):
        decode_group_runtime_snapshot(snapshot, planned_alarms=(plan('other'),))


def test_encode_rejects_snapshot_chain_mismatch() -> None:
    state = group_state(active_runtime_state())
    current = materialization(
        state,
        previous_commit_id='C-expected',
        runtime_state_updates=(identity(),),
    )
    previous = GroupRuntimeSnapshot(
        {
            'snapshot_schema_version': 'group-runtime-snapshot.v1',
            'priority_group': 'mill-feed',
            'last_commit_id': 'C-other',
            'alarms': {},
        }
    )

    with pytest.raises(AlarmRuntimeCompositionError, match='previous_commit_id'):
        encode_group_runtime_snapshot(
            state,
            commit=current.commit,
            previous_snapshot=previous,
        )


def test_encode_rejects_runtime_update_list_that_does_not_match_hot_state_delta() -> None:
    state = group_state(active_runtime_state())
    current = materialization(
        state,
        runtime_state_updates=(AlarmIdentity('mill', 'other'),),
        affected_alarms=(AlarmIdentity('mill', 'other'),),
    )

    with pytest.raises(AlarmRuntimeCompositionError, match='provenance'):
        encode_group_runtime_snapshot(
            state,
            commit=current.commit,
            previous_snapshot=None,
        )


def test_deactivation_only_state_round_trip_does_not_require_episode() -> None:
    runtime = AlarmRuntimeState(
        alarm_identity=identity(),
        deactivation_effect=DeactivationEffect(
            effect_id='D1',
            effective_from=NOW,
            effective_until=NOW + timedelta(hours=1),
        ),
    )
    state = GroupLifecycleState(priority_group='mill-feed', alarms=(runtime,))
    current = materialization(state, runtime_state_updates=(identity(),))

    snapshot = encode_group_runtime_snapshot(
        state,
        commit=current.commit,
        previous_snapshot=None,
    )

    assert 'episode' not in snapshot.as_document()
    assert decode_group_runtime_snapshot(snapshot, planned_alarms=(plan(),)) == state


def test_decode_rejects_duplicate_canonical_identity_in_current_configuration() -> None:
    state = group_state(active_runtime_state())
    current = materialization(state, runtime_state_updates=(identity(),))
    snapshot = encode_group_runtime_snapshot(
        state,
        commit=current.commit,
        previous_snapshot=None,
    )

    with pytest.raises(AlarmRuntimeCompositionError, match='duplicate canonical'):
        decode_group_runtime_snapshot(snapshot, planned_alarms=(plan(), plan()))


def test_encode_rejects_episode_transition_not_declared_by_engine_commit() -> None:
    state = group_state(active_runtime_state())
    current = materialization(state, runtime_state_updates=(identity(),))
    invalid = GroupCommitMaterialization(
        state=current.state,
        commit=replace(current.commit, episode_change=None),
        records=current.records,
    )

    with pytest.raises(AlarmRuntimeCompositionError, match='episode_change'):
        encode_group_runtime_snapshot(
            state,
            commit=invalid.commit,
            previous_snapshot=None,
        )
