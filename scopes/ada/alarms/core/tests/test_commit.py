import json
from dataclasses import replace
from datetime import timedelta

import pytest

from ada.alarms.core import (
    AlarmContractError,
    AlarmRuntimeState,
    AlarmStatus,
    AssignmentChangeKind,
    ConfigurationClosure,
    DeactivationDecisionKind,
    EvidenceContractRef,
    GroupLifecycleState,
    InputKind,
    ManagementActionOutcome,
    OccurrenceClosureReason,
    PriorityDisposition,
    RoutingDestination,
    commit_id_for,
    cycle_id_for,
    materialize_group_commit,
    reduce_group_cycle,
    reset_group_for_reconfiguration,
)

from .support import (
    NOW,
    Ids,
    deactivation_decision,
    deactivation_request,
    error,
    identity,
    management_action,
    physical,
    plan,
    reappear_after,
)

TECHNICAL_EVIDENCE = EvidenceContractRef(
    contract_key='technical-evaluation-test',
    contract_version='v1',
)


def _reduce(
    state,
    plans,
    evaluations,
    *,
    at=NOW,
    closures=(),
    actions=(),
    pending_requests=(),
    decisions=(),
    ids=None,
):
    generated = ids or Ids()
    return reduce_group_cycle(
        state,
        cycle_at=at,
        planned_alarms=plans,
        evaluations=evaluations,
        configuration_closures=closures,
        management_actions=actions,
        pending_deactivation_requests=pending_requests,
        deactivation_decisions=decisions,
        occurrence_id_factory=generated.new_occurrence,
        episode_id_factory=generated.new_episode,
        management_effect_id_factory=generated.new_management_effect,
        reappearance_due_at_resolver=reappear_after(300),
        deactivation_request_id_factory=generated.new_deactivation_request,
        deactivation_effect_id_factory=generated.new_deactivation_effect,
    )


def _materialize(previous, decision, evaluations, *, at=NOW, previous_commit_id=None, **kwargs):
    kwargs.setdefault('technical_evidence_contract', TECHNICAL_EVIDENCE)
    return materialize_group_commit(
        previous,
        decision,
        evaluations=evaluations,
        cycle_at=at,
        committed_at=at + timedelta(milliseconds=10),
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        runtime_artifact_version='ada-alarms-runtime-test',
        previous_commit_id=previous_commit_id,
        **kwargs,
    )


def _started():
    ids = Ids()
    alarm = plan('risk')
    previous = GroupLifecycleState(priority_group='mill-feed')
    evaluation = physical('risk', AlarmStatus.ACTIVE)
    decision = _reduce(previous, (alarm,), (evaluation,), ids=ids)
    materialized = _materialize(previous, decision, (evaluation,))
    assert materialized is not None
    return alarm, ids, materialized


def test_cycle_and_commit_ids_are_deterministic_and_legible() -> None:
    cycle_id = cycle_id_for(NOW)
    assert cycle_id == '20260824T140000000000Z'
    assert commit_id_for(cycle_id, 'mill-feed') == f'{cycle_id}__mill-feed'


def test_occurrence_start_materializes_initial_evidence_and_next_due() -> None:
    alarm, _, materialized = _started()
    runtime = materialized.state.get(alarm.identity)
    assert runtime is not None
    assert runtime.next_evidence_due_at == NOW + timedelta(seconds=300)
    assert len(materialized.records.evidence_records) == 1
    evidence = materialized.records.evidence_records[0]
    assert evidence.evidence_id == 'evidence:O1:initial'
    assert evidence.evaluation.status is AlarmStatus.ACTIVE
    assert 'occurrence_started' in {
        event.event_key for event in materialized.records.journey_events
    }
    assert materialized.commit.affected_alarms == (alarm.identity,)
    assert alarm.identity in materialized.commit.runtime_state_updates


def test_steady_active_without_due_work_produces_no_commit() -> None:
    alarm, ids, started = _started()
    at = NOW + timedelta(minutes=1)
    evaluation = physical('risk', AlarmStatus.ACTIVE, at=at)
    decision = _reduce(started.state, (alarm,), (evaluation,), at=at, ids=ids)
    assert (
        _materialize(
            started.state,
            decision,
            (evaluation,),
            at=at,
            previous_commit_id=started.commit.commit_id,
        )
        is None
    )


def test_periodic_evidence_due_creates_commit_without_lifecycle_change() -> None:
    alarm, ids, started = _started()
    at = NOW + timedelta(minutes=5)
    evaluation = physical('risk', AlarmStatus.ACTIVE, at=at)
    decision = _reduce(started.state, (alarm,), (evaluation,), at=at, ids=ids)
    materialized = _materialize(
        started.state,
        decision,
        (evaluation,),
        at=at,
        previous_commit_id=started.commit.commit_id,
    )
    assert materialized is not None
    assert decision.has_lifecycle_change is False
    assert len(materialized.records.evidence_records) == 1
    assert materialized.records.evidence_records[0].evidence_id.endswith('2026-08-24T14:05:00Z')
    runtime = materialized.state.get(alarm.identity)
    assert runtime is not None
    assert runtime.last_evaluation == evaluation
    assert runtime.next_evidence_due_at == at + timedelta(minutes=5)


def test_downtime_does_not_reconstruct_missed_periodic_evidence() -> None:
    alarm, ids, started = _started()
    at = NOW + timedelta(minutes=20)
    evaluation = physical('risk', AlarmStatus.ACTIVE, at=at)
    decision = _reduce(started.state, (alarm,), (evaluation,), at=at, ids=ids)
    materialized = _materialize(
        started.state,
        decision,
        (evaluation,),
        at=at,
        previous_commit_id=started.commit.commit_id,
    )
    assert materialized is not None
    assert len(materialized.records.evidence_records) == 1
    assert materialized.records.evidence_records[0].evidence_id.endswith('2026-08-24T14:05:00Z')
    runtime = materialized.state.get(alarm.identity)
    assert runtime is not None
    assert runtime.next_evidence_due_at == at + timedelta(minutes=5)


def test_custom_global_evidence_interval_is_applied_without_per_alarm_override() -> None:
    ids = Ids()
    alarm = plan('risk')
    previous = GroupLifecycleState(priority_group='mill-feed')
    evaluation = physical('risk', AlarmStatus.ACTIVE)
    decision = _reduce(previous, (alarm,), (evaluation,), ids=ids)
    materialized = _materialize(
        previous,
        decision,
        (evaluation,),
        evidence_sampling_interval_seconds=120,
    )
    assert materialized is not None
    runtime = materialized.state.get(alarm.identity)
    assert runtime is not None
    assert runtime.next_evidence_due_at == NOW + timedelta(seconds=120)


def test_normal_close_always_materializes_final_physical_evidence() -> None:
    alarm, ids, started = _started()
    at = NOW + timedelta(minutes=3)
    evaluation = physical('risk', AlarmStatus.INACTIVE, at=at)
    decision = _reduce(started.state, (alarm,), (evaluation,), at=at, ids=ids)
    materialized = _materialize(
        started.state,
        decision,
        (evaluation,),
        at=at,
        previous_commit_id=started.commit.commit_id,
    )
    assert materialized is not None
    assert len(materialized.records.evidence_records) == 1
    evidence = materialized.records.evidence_records[0]
    assert evidence.evaluation.status is AlarmStatus.INACTIVE
    assert ':final:' in evidence.evidence_id
    assert 'occurrence_closed' in {event.event_key for event in materialized.records.journey_events}


def test_technical_hold_start_records_technical_evidence_and_suspends_cadence() -> None:
    alarm, ids, started = _started()
    at = NOW + timedelta(minutes=1)
    evaluation = error('risk', at=at)
    decision = _reduce(started.state, (alarm,), (evaluation,), at=at, ids=ids)
    materialized = _materialize(
        started.state,
        decision,
        (evaluation,),
        at=at,
        previous_commit_id=started.commit.commit_id,
    )
    assert materialized is not None
    runtime = materialized.state.get(alarm.identity)
    assert runtime is not None
    assert runtime.technical_hold is not None
    assert runtime.next_evidence_due_at is None
    assert materialized.records.evidence_records[0].evaluation.status is AlarmStatus.ERROR
    assert 'technical_hold_started' in {
        event.event_key for event in materialized.records.journey_events
    }


def test_repeated_error_inside_hold_without_new_durable_fact_is_noop() -> None:
    alarm, ids, started = _started()
    error_at = NOW + timedelta(minutes=1)
    first_error = error('risk', at=error_at)
    first_decision = _reduce(
        started.state,
        (alarm,),
        (first_error,),
        at=error_at,
        ids=ids,
    )
    hold = _materialize(
        started.state,
        first_decision,
        (first_error,),
        at=error_at,
        previous_commit_id=started.commit.commit_id,
    )
    assert hold is not None
    at = error_at + timedelta(minutes=1)
    repeated = error('risk', at=at)
    decision = _reduce(hold.state, (alarm,), (repeated,), at=at, ids=ids)
    assert (
        _materialize(
            hold.state,
            decision,
            (repeated,),
            at=at,
            previous_commit_id=hold.commit.commit_id,
        )
        is None
    )


def test_technical_hold_recovery_records_immediate_evidence_and_reanchors() -> None:
    alarm, ids, started = _started()
    error_at = NOW + timedelta(minutes=1)
    first_error = error('risk', at=error_at)
    hold_decision = _reduce(started.state, (alarm,), (first_error,), at=error_at, ids=ids)
    hold = _materialize(
        started.state,
        hold_decision,
        (first_error,),
        at=error_at,
        previous_commit_id=started.commit.commit_id,
    )
    assert hold is not None
    at = error_at + timedelta(minutes=1)
    recovered = physical('risk', AlarmStatus.ACTIVE, at=at)
    decision = _reduce(hold.state, (alarm,), (recovered,), at=at, ids=ids)
    materialized = _materialize(
        hold.state,
        decision,
        (recovered,),
        at=at,
        previous_commit_id=hold.commit.commit_id,
    )
    assert materialized is not None
    runtime = materialized.state.get(alarm.identity)
    assert runtime is not None and runtime.technical_hold is None
    assert runtime.next_evidence_due_at == at + timedelta(minutes=5)
    assert ':recovery:' in materialized.records.evidence_records[0].evidence_id
    assert 'technical_hold_recovered' in {
        event.event_key for event in materialized.records.journey_events
    }


def test_management_additional_creates_receipt_even_without_new_effect() -> None:
    alarm, ids, started = _started()
    first_at = NOW + timedelta(minutes=1)
    first_eval = physical('risk', AlarmStatus.ACTIVE, at=first_at)
    first_decision = _reduce(
        started.state,
        (alarm,),
        (first_eval,),
        at=first_at,
        actions=(management_action('risk', at=first_at),),
        ids=ids,
    )
    first = _materialize(
        started.state,
        first_decision,
        (first_eval,),
        at=first_at,
        previous_commit_id=started.commit.commit_id,
    )
    assert first is not None
    second_at = first_at + timedelta(minutes=1)
    second_eval = physical('risk', AlarmStatus.ACTIVE, at=second_at)
    second_decision = _reduce(
        first.state,
        (alarm,),
        (second_eval,),
        at=second_at,
        actions=(management_action('risk', input_id='M2', at=second_at),),
        ids=ids,
    )
    assert (
        second_decision.management_action_results[0].outcome is ManagementActionOutcome.ADDITIONAL
    )
    second = _materialize(
        first.state,
        second_decision,
        (second_eval,),
        at=second_at,
        previous_commit_id=first.commit.commit_id,
    )
    assert second is not None
    receipt = second.records.input_receipts[0]
    assert receipt.input_kind is InputKind.MANAGEMENT
    assert receipt.input_id == 'M2'
    assert receipt.outcome == 'ADDITIONAL'
    assert receipt.commit_id == second.commit.commit_id


def test_deactivation_intent_uses_one_deactivation_request_receipt_for_same_input() -> None:
    ids = Ids()
    alarm = plan('risk', deactivation_approval_required=False)
    previous = GroupLifecycleState(priority_group='mill-feed')
    initial_eval = physical('risk', AlarmStatus.ACTIVE)
    initial_decision = _reduce(previous, (alarm,), (initial_eval,), ids=ids)
    started = _materialize(previous, initial_decision, (initial_eval,))
    assert started is not None
    at = NOW + timedelta(minutes=1)
    evaluation = physical('risk', AlarmStatus.ACTIVE, at=at)
    decision = _reduce(
        started.state,
        (alarm,),
        (evaluation,),
        at=at,
        actions=(
            management_action(
                'risk',
                at=at,
                deactivation_until=at + timedelta(minutes=30),
            ),
        ),
        ids=ids,
    )
    materialized = _materialize(
        started.state,
        decision,
        (evaluation,),
        at=at,
        previous_commit_id=started.commit.commit_id,
    )
    assert materialized is not None
    assert len(materialized.records.input_receipts) == 1
    receipt = materialized.records.input_receipts[0]
    assert receipt.input_kind is InputKind.DEACTIVATION_REQUEST
    assert receipt.input_id == 'M1'
    assert receipt.outcome == 'DIRECT'


def test_deactivation_decision_has_own_receipt() -> None:
    ids = Ids()
    alarm = plan('risk', deactivation_approval_required=True)
    previous = GroupLifecycleState(priority_group='mill-feed')
    initial_eval = physical('risk', AlarmStatus.ACTIVE)
    initial_decision = _reduce(previous, (alarm,), (initial_eval,), ids=ids)
    started = _materialize(previous, initial_decision, (initial_eval,))
    assert started is not None
    pending = deactivation_request(requested_at=NOW + timedelta(minutes=1))
    at = NOW + timedelta(minutes=2)
    evaluation = physical('risk', AlarmStatus.ACTIVE, at=at)
    decision = _reduce(
        started.state,
        (alarm,),
        (evaluation,),
        at=at,
        pending_requests=(pending,),
        decisions=(deactivation_decision(kind=DeactivationDecisionKind.REJECTED, at=at),),
        ids=ids,
    )
    materialized = _materialize(
        started.state,
        decision,
        (evaluation,),
        at=at,
        previous_commit_id=started.commit.commit_id,
    )
    assert materialized is not None
    receipt = materialized.records.input_receipts[0]
    assert receipt.input_kind is InputKind.DEACTIVATION_DECISION
    assert receipt.input_id == 'DD1'
    assert receipt.outcome == 'REJECTED'


def test_evidence_and_receipt_documents_are_plain_serializable_values() -> None:
    _, _, started = _started()
    evidence = started.records.evidence_records[0].as_document()
    receipt_document = {
        receipt.receipt_id: receipt.as_document() for receipt in started.records.input_receipts
    }
    assert evidence['contract_key'] == 'threshold'
    assert evidence['payload'] == {'value': 10.0}
    assert receipt_document == {}
    assert started.commit.as_document()['affected_alarms'] == ['mill/risk']


def test_runtime_state_rejects_evidence_deadline_without_occurrence() -> None:
    with pytest.raises(ValueError, match='requires an open occurrence'):
        AlarmRuntimeState(
            alarm_identity=identity('risk'),
            next_evidence_due_at=NOW + timedelta(minutes=5),
        )


def test_runtime_state_rejects_normal_evidence_deadline_during_technical_hold() -> None:
    _, _, started = _started()
    runtime = started.state.get(identity('risk'))
    assert runtime is not None
    at = NOW + timedelta(minutes=1)
    alarm = plan('risk')
    evaluation = error('risk', at=at)
    decision = _reduce(started.state, (alarm,), (evaluation,), at=at, ids=Ids())
    hold = decision.state.get(identity('risk'))
    assert hold is not None and hold.technical_hold is not None
    with pytest.raises(ValueError, match='suspends next_evidence_due_at'):
        replace(hold, next_evidence_due_at=NOW + timedelta(minutes=5))


def test_materialization_rejects_evaluation_outside_frozen_cycle() -> None:
    alarm = plan('risk')
    previous = GroupLifecycleState(priority_group='mill-feed')
    evaluation = physical('risk', AlarmStatus.ACTIVE)
    decision = _reduce(previous, (alarm,), (evaluation,))
    with pytest.raises(AlarmContractError, match='frozen cycle_at'):
        materialize_group_commit(
            previous,
            decision,
            evaluations=(evaluation,),
            cycle_at=NOW + timedelta(seconds=1),
            committed_at=NOW + timedelta(seconds=2),
            alarm_configuration_revision='R42',
            tool_registry_revision='T18',
            runtime_artifact_version='test',
        )


def test_priority_transition_journey_is_emitted_only_when_prior_resolution_is_supplied() -> None:
    ids = Ids()
    risk = plan('risk', priority_order=2)
    impact = plan('impact', priority_order=1)
    previous = GroupLifecycleState(priority_group='mill-feed')
    first_evaluations = (
        physical('risk', AlarmStatus.ACTIVE),
        physical('impact', AlarmStatus.INACTIVE),
    )
    first_decision = _reduce(previous, (risk, impact), first_evaluations, ids=ids)
    first = _materialize(previous, first_decision, first_evaluations)
    assert first is not None
    at = NOW + timedelta(minutes=1)
    evaluations = (
        physical('risk', AlarmStatus.ACTIVE, at=at),
        physical('impact', AlarmStatus.ACTIVE, at=at),
    )
    second_decision = _reduce(first.state, (risk, impact), evaluations, at=at, ids=ids)
    without_prior = _materialize(
        first.state,
        second_decision,
        evaluations,
        at=at,
        previous_commit_id=first.commit.commit_id,
    )
    assert without_prior is not None
    assert 'priority_suppressed' not in {
        event.event_key for event in without_prior.records.journey_events
    }
    with_prior = _materialize(
        first.state,
        second_decision,
        evaluations,
        at=at,
        previous_commit_id=first.commit.commit_id,
        previous_priority_resolution=first_decision.priority_resolution,
    )
    assert with_prior is not None
    assert 'priority_suppressed' in {event.event_key for event in with_prior.records.journey_events}
    current = second_decision.priority_resolution
    assert current is not None
    risk_decision = next(item for item in current.alarms if item.alarm_identity == risk.identity)
    assert risk_decision.disposition is PriorityDisposition.ECLIPSED


def test_pending_dependency_decision_has_no_receipt_and_no_commit_by_itself() -> None:
    ids = Ids()
    alarm = plan('risk', deactivation_approval_required=True)
    previous = GroupLifecycleState(priority_group='mill-feed')
    initial_eval = physical('risk', AlarmStatus.ACTIVE)
    initial_decision = _reduce(previous, (alarm,), (initial_eval,), ids=ids)
    started = _materialize(previous, initial_decision, (initial_eval,))
    assert started is not None
    at = NOW + timedelta(minutes=1)
    evaluation = physical('risk', AlarmStatus.ACTIVE, at=at)
    decision = _reduce(
        started.state,
        (alarm,),
        (evaluation,),
        at=at,
        decisions=(deactivation_decision(request_id='missing', at=at),),
        ids=ids,
    )
    assert (
        _materialize(
            started.state,
            decision,
            (evaluation,),
            at=at,
            previous_commit_id=started.commit.commit_id,
        )
        is None
    )


def test_technical_evidence_contract_is_never_inferred() -> None:
    alarm, ids, started = _started()
    at = NOW + timedelta(minutes=1)
    evaluation = error('risk', at=at)
    decision = _reduce(started.state, (alarm,), (evaluation,), at=at, ids=ids)
    with pytest.raises(AlarmContractError, match='explicit contract'):
        materialize_group_commit(
            started.state,
            decision,
            evaluations=(evaluation,),
            cycle_at=at,
            committed_at=at + timedelta(milliseconds=10),
            alarm_configuration_revision='R42',
            tool_registry_revision='T18',
            runtime_artifact_version='test',
            previous_commit_id=started.commit.commit_id,
        )


def test_assignment_records_keep_stable_identity_and_route_removal_journey() -> None:
    ids = Ids()
    original = plan(
        'risk',
        destinations=(
            RoutingDestination('tool-b', 0),
            RoutingDestination('tool-c', 1800),
        ),
    )
    previous = GroupLifecycleState(priority_group='mill-feed')
    initial_evaluation = physical('risk', AlarmStatus.ACTIVE)
    initial_decision = _reduce(previous, (original,), (initial_evaluation,), ids=ids)
    started = _materialize(previous, initial_decision, (initial_evaluation,))
    assert started is not None
    at = NOW + timedelta(minutes=5)
    changed = plan('risk', destinations=())
    evaluation = physical('risk', AlarmStatus.ACTIVE, at=at)
    decision = _reduce(started.state, (changed,), (evaluation,), at=at, ids=ids)
    materialized = _materialize(
        started.state,
        decision,
        (evaluation,),
        at=at,
        previous_commit_id=started.commit.commit_id,
    )
    assert materialized is not None
    removed = [
        record
        for record in materialized.records.assignment_changes
        if record.kind is AssignmentChangeKind.REMOVED
    ]
    assert {record.tool_key for record in removed} == {'tool-b', 'tool-c'}
    assert all(record.occurrence_id == 'O1' for record in removed)
    assert set(materialized.commit.assignment_change_ids) >= {
        record.change_id for record in removed
    }
    journey = {event.event_key for event in materialized.records.journey_events}
    assert 'tool_assignment_removed' in journey


def test_deactivation_expiry_keeps_effect_identity_and_emits_journey() -> None:
    ids = Ids()
    alarm = plan('risk', deactivation_approval_required=False)
    previous = GroupLifecycleState(priority_group='mill-feed')
    initial_evaluation = physical('risk', AlarmStatus.ACTIVE)
    initial_decision = _reduce(previous, (alarm,), (initial_evaluation,), ids=ids)
    started = _materialize(previous, initial_decision, (initial_evaluation,))
    assert started is not None
    managed_at = NOW + timedelta(minutes=1)
    until = managed_at + timedelta(minutes=2)
    managed_evaluation = physical('risk', AlarmStatus.ACTIVE, at=managed_at)
    managed_decision = _reduce(
        started.state,
        (alarm,),
        (managed_evaluation,),
        at=managed_at,
        actions=(management_action('risk', at=managed_at, deactivation_until=until),),
        ids=ids,
    )
    managed = _materialize(
        started.state,
        managed_decision,
        (managed_evaluation,),
        at=managed_at,
        previous_commit_id=started.commit.commit_id,
    )
    assert managed is not None
    runtime = managed.state.get(alarm.identity)
    assert runtime is not None and runtime.deactivation_effect is not None
    effect_id = runtime.deactivation_effect.effect_id
    expired_evaluation = physical('risk', AlarmStatus.ACTIVE, at=until)
    expired_decision = _reduce(
        managed.state,
        (alarm,),
        (expired_evaluation,),
        at=until,
        ids=ids,
    )
    expired = _materialize(
        managed.state,
        expired_decision,
        (expired_evaluation,),
        at=until,
        previous_commit_id=managed.commit.commit_id,
    )
    assert expired is not None
    cleared = expired.records.deactivation_effects[0]
    assert cleared.effect_id == effect_id
    assert cleared.effective_until == until
    assert expired.commit.deactivation_effect_ids == (effect_id,)
    assert 'deactivation_expired' in {event.event_key for event in expired.records.journey_events}


def test_engine_commit_records_document_contains_full_immutable_payloads() -> None:
    alarm, ids, started = _started()
    at = NOW + timedelta(minutes=1)
    evaluation = physical('risk', AlarmStatus.ACTIVE, at=at)
    decision = _reduce(
        started.state,
        (alarm,),
        (evaluation,),
        at=at,
        actions=(management_action('risk', at=at),),
        ids=ids,
    )
    materialized = _materialize(
        started.state,
        decision,
        (evaluation,),
        at=at,
        previous_commit_id=started.commit.commit_id,
    )
    assert materialized is not None
    document = materialized.records.as_document()
    effect = document['management_effects'][0]
    assert effect['effect_id'] == 'ME1'
    assert effect['source_occurrence_id'] == 'O1'
    assert effect['reappearance_due_at'].endswith('Z')
    assert document['input_receipts'][0]['commit_id'] == materialized.commit.commit_id


def test_configuration_close_uses_current_physical_evaluation_for_final_evidence() -> None:
    alarm, ids, started = _started()
    at = NOW + timedelta(minutes=2)
    evaluation = physical('risk', AlarmStatus.ACTIVE, at=at)
    closure = ConfigurationClosure(
        alarm_identity=alarm.identity,
        reason=OccurrenceClosureReason.CONFIGURATION_DISABLED,
        effective_at=at,
    )
    decision = _reduce(
        started.state,
        (),
        (evaluation,),
        at=at,
        closures=(closure,),
        ids=ids,
    )
    materialized = _materialize(
        started.state,
        decision,
        (evaluation,),
        at=at,
        previous_commit_id=started.commit.commit_id,
    )
    assert materialized is not None
    assert len(materialized.records.evidence_records) == 1
    evidence = materialized.records.evidence_records[0]
    assert evidence.evaluation.status is AlarmStatus.ACTIVE
    assert ':final:' in evidence.evidence_id


def test_structural_reset_without_evaluation_never_fabricates_final_evidence() -> None:
    _, _, started = _started()
    at = NOW + timedelta(minutes=2)
    decision = reset_group_for_reconfiguration(started.state, effective_at=at)
    materialized = _materialize(
        started.state,
        decision,
        (),
        at=at,
        previous_commit_id=started.commit.commit_id,
    )
    assert materialized is not None
    assert materialized.records.evidence_records == ()
    assert 'occurrence_closed' in {event.event_key for event in materialized.records.journey_events}


def test_structural_reset_clears_deactivation_without_calling_it_expiry() -> None:
    ids = Ids()
    alarm = plan('risk', deactivation_approval_required=False)
    previous = GroupLifecycleState(priority_group='mill-feed')
    initial_evaluation = physical('risk', AlarmStatus.ACTIVE)
    initial_decision = _reduce(previous, (alarm,), (initial_evaluation,), ids=ids)
    started = _materialize(previous, initial_decision, (initial_evaluation,))
    assert started is not None
    managed_at = NOW + timedelta(minutes=1)
    until = managed_at + timedelta(minutes=30)
    evaluation = physical('risk', AlarmStatus.ACTIVE, at=managed_at)
    deactivated_decision = _reduce(
        started.state,
        (alarm,),
        (evaluation,),
        at=managed_at,
        actions=(management_action('risk', at=managed_at, deactivation_until=until),),
        ids=ids,
    )
    deactivated = _materialize(
        started.state,
        deactivated_decision,
        (evaluation,),
        at=managed_at,
        previous_commit_id=started.commit.commit_id,
    )
    assert deactivated is not None
    reset_at = managed_at + timedelta(minutes=2)
    reset = reset_group_for_reconfiguration(deactivated.state, effective_at=reset_at)
    materialized = _materialize(
        deactivated.state,
        reset,
        (),
        at=reset_at,
        previous_commit_id=deactivated.commit.commit_id,
    )
    assert materialized is not None
    journey = {event.event_key for event in materialized.records.journey_events}
    assert 'deactivation_cleared' in journey
    assert 'deactivation_expired' not in journey


def test_overdue_hold_does_not_attach_fresh_inactive_evidence_to_closed_occurrence() -> None:
    alarm, ids, started = _started()
    error_at = NOW + timedelta(minutes=1)
    first_error = error('risk', at=error_at)
    hold_decision = _reduce(started.state, (alarm,), (first_error,), at=error_at, ids=ids)
    hold = _materialize(
        started.state,
        hold_decision,
        (first_error,),
        at=error_at,
        previous_commit_id=started.commit.commit_id,
    )
    assert hold is not None
    at = error_at + timedelta(seconds=320)
    evaluation = physical('risk', AlarmStatus.INACTIVE, at=at)
    decision = _reduce(hold.state, (alarm,), (evaluation,), at=at, ids=ids)
    assert decision.occurrence_changes[0].occurrence.ended_at == error_at + timedelta(seconds=300)
    materialized = _materialize(
        hold.state,
        decision,
        (evaluation,),
        at=at,
        previous_commit_id=hold.commit.commit_id,
    )
    assert materialized is not None
    assert materialized.records.evidence_records == ()
    journey = {event.event_key for event in materialized.records.journey_events}
    assert 'technical_hold_expired' in journey
    assert 'occurrence_closed' in journey


def test_commit_and_records_documents_are_json_serializable() -> None:
    _, _, started = _started()
    json.dumps(started.commit.as_document(), sort_keys=True)
    json.dumps(started.records.as_document(), sort_keys=True)
