from datetime import timedelta

import pytest

from ada.alarms.core import (
    AlarmContractError,
    AlarmKind,
    AlarmStatus,
    DeactivationDecisionKind,
    DeactivationDecisionOutcome,
    DeactivationRequestOutcome,
    GroupLifecycleState,
    ManagementActionOutcome,
    PriorityDisposition,
    RoutingDestination,
    ToolAssignment,
    is_deactivated,
    reduce_group_cycle,
    reset_group_for_reconfiguration,
)

from .support import (
    NOW,
    Ids,
    deactivation_decision,
    deactivation_request,
    identity,
    management_action,
    physical,
    plan,
    reappear_after,
)


def _reduce(
    state: GroupLifecycleState,
    plans,
    evaluations,
    *,
    at=NOW,
    actions=(),
    pending_requests=(),
    decisions=(),
    ids: Ids | None = None,
    reappearance_seconds=300,
):
    generated = ids or Ids()
    return reduce_group_cycle(
        state,
        cycle_at=at,
        planned_alarms=plans,
        evaluations=evaluations,
        management_actions=actions,
        pending_deactivation_requests=pending_requests,
        deactivation_decisions=decisions,
        occurrence_id_factory=generated.new_occurrence,
        episode_id_factory=generated.new_episode,
        management_effect_id_factory=generated.new_management_effect,
        reappearance_due_at_resolver=reappear_after(reappearance_seconds),
        deactivation_request_id_factory=generated.new_deactivation_request,
        deactivation_effect_id_factory=generated.new_deactivation_effect,
    )


def _start(*, approval_required: bool = False, planned=None):
    ids = Ids()
    alarm = planned or plan('risk', deactivation_approval_required=approval_required)
    started = _reduce(
        GroupLifecycleState(priority_group='mill-feed'),
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE),),
        ids=ids,
    )
    return started, ids, alarm


def test_direct_deactivation_materializes_effect_from_management_intent() -> None:
    started, ids, alarm = _start(approval_required=False)
    at = NOW + timedelta(minutes=1)
    until = at + timedelta(minutes=30)
    decision = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=at),),
        at=at,
        actions=(management_action('risk', at=at, deactivation_until=until),),
        ids=ids,
    )
    result = decision.deactivation_request_results[0]
    runtime = decision.state.get(identity('risk'))
    assert result.outcome is DeactivationRequestOutcome.DIRECT
    assert result.deactivation_request is not None
    assert result.deactivation_effect_id == 'DE1'
    assert runtime is not None and runtime.deactivation_effect is not None
    assert runtime.deactivation_effect.effective_from == at
    assert runtime.deactivation_effect.effective_until == until
    assert is_deactivated(runtime, at=at)
    assert decision.priority_resolution is not None
    assert decision.priority_resolution.alarms[0].disposition is PriorityDisposition.DEACTIVATED


def test_approval_required_creates_pending_request_without_effect() -> None:
    started, ids, alarm = _start(approval_required=True)
    at = NOW + timedelta(minutes=1)
    decision = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=at),),
        at=at,
        actions=(
            management_action(
                'risk',
                at=at,
                deactivation_until=at + timedelta(hours=1),
            ),
        ),
        ids=ids,
    )
    result = decision.deactivation_request_results[0]
    runtime = decision.state.get(identity('risk'))
    assert result.outcome is DeactivationRequestOutcome.PENDING_APPROVAL
    assert result.deactivation_request is not None
    assert result.deactivation_request.request_id == 'DR1'
    assert runtime is not None and runtime.deactivation_effect is None
    assert decision.deactivation_effect_changes == ()


def test_approval_decision_materializes_same_deactivation_effect_contract() -> None:
    started, ids, alarm = _start(approval_required=True)
    requested_at = NOW + timedelta(minutes=1)
    pending = deactivation_request(
        requested_at=requested_at,
        effective_until=requested_at + timedelta(hours=1),
    )
    at = requested_at + timedelta(minutes=2)
    decision = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=at),),
        at=at,
        pending_requests=(pending,),
        decisions=(deactivation_decision(at=at),),
        ids=ids,
    )
    result = decision.deactivation_decision_results[0]
    runtime = decision.state.get(identity('risk'))
    assert result.outcome is DeactivationDecisionOutcome.APPLIED
    assert result.deactivation_effect_id == 'DE1'
    assert runtime is not None and runtime.deactivation_effect is not None
    assert runtime.deactivation_effect.effective_from == at
    assert runtime.deactivation_effect.effective_until == pending.effective_until


def test_rejected_request_is_terminal_and_does_not_materialize_effect() -> None:
    started, ids, alarm = _start(approval_required=True)
    pending = deactivation_request(requested_at=NOW + timedelta(minutes=1))
    at = NOW + timedelta(minutes=2)
    decision = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=at),),
        at=at,
        pending_requests=(pending,),
        decisions=(
            deactivation_decision(
                kind=DeactivationDecisionKind.REJECTED,
                at=at,
            ),
        ),
        ids=ids,
    )
    result = decision.deactivation_decision_results[0]
    runtime = decision.state.get(identity('risk'))
    assert result.outcome is DeactivationDecisionOutcome.REJECTED
    assert runtime is not None and runtime.deactivation_effect is None


def test_later_request_does_not_create_parallel_workflow() -> None:
    started, ids, alarm = _start(approval_required=True)
    pending = deactivation_request(requested_at=NOW + timedelta(minutes=1))
    at = NOW + timedelta(minutes=2)
    decision = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=at),),
        at=at,
        pending_requests=(pending,),
        actions=(
            management_action(
                'risk',
                input_id='M2',
                at=at,
                deactivation_until=at + timedelta(hours=1),
            ),
        ),
        ids=ids,
    )
    assert decision.deactivation_request_results[0].outcome is DeactivationRequestOutcome.ADDITIONAL
    assert ids.deactivation_request == 0


def test_new_explicit_request_after_rejection_can_become_candidate() -> None:
    started, ids, alarm = _start(approval_required=True)
    first_at = NOW + timedelta(minutes=1)
    pending = deactivation_request(requested_at=first_at)
    rejected_at = first_at + timedelta(minutes=1)
    new_at = rejected_at + timedelta(minutes=1)
    decision = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=new_at),),
        at=new_at,
        pending_requests=(pending,),
        decisions=(
            deactivation_decision(
                kind=DeactivationDecisionKind.REJECTED,
                at=rejected_at,
            ),
        ),
        actions=(
            management_action(
                'risk',
                input_id='M2',
                at=new_at,
                deactivation_until=new_at + timedelta(hours=1),
            ),
        ),
        ids=ids,
    )
    assert decision.deactivation_decision_results[0].outcome is DeactivationDecisionOutcome.REJECTED
    request_result = decision.deactivation_request_results[0]
    assert request_result.outcome is DeactivationRequestOutcome.PENDING_APPROVAL
    assert request_result.deactivation_request is not None
    assert request_result.deactivation_request.request_id == 'DR1'


def test_decision_without_request_remains_pending_dependency() -> None:
    started, ids, alarm = _start(approval_required=True)
    at = NOW + timedelta(minutes=1)
    decision = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=at),),
        at=at,
        decisions=(deactivation_decision(request_id='missing', at=at),),
        ids=ids,
    )
    assert (
        decision.deactivation_decision_results[0].outcome
        is DeactivationDecisionOutcome.PENDING_DEPENDENCY
    )


def test_approval_for_previous_occurrence_is_rejected_as_stale_target() -> None:
    started, ids, alarm = _start(approval_required=True)
    old_request = deactivation_request(
        occurrence_id='OLD',
        requested_at=NOW - timedelta(minutes=1),
    )
    at = NOW + timedelta(minutes=1)
    decision = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=at),),
        at=at,
        pending_requests=(old_request,),
        decisions=(deactivation_decision(at=at),),
        ids=ids,
    )
    assert (
        decision.deactivation_decision_results[0].outcome
        is DeactivationDecisionOutcome.STALE_TARGET
    )


def test_deactivation_unavailable_when_alarm_has_no_policy() -> None:
    started, ids, alarm = _start(planned=plan('risk'))
    at = NOW + timedelta(minutes=1)
    decision = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=at),),
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
    assert (
        decision.deactivation_request_results[0].outcome is DeactivationRequestOutcome.UNAVAILABLE
    )


def test_shadow_alarm_cannot_request_operational_deactivation() -> None:
    alarm = plan(
        'risk',
        delivery_enabled=False,
        deactivation_approval_required=False,
    )
    started, ids, _ = _start(planned=alarm)
    at = NOW + timedelta(minutes=1)
    decision = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=at),),
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
    assert decision.management_action_results[0].outcome is ManagementActionOutcome.LATE
    assert decision.deactivation_request_results[0].outcome is DeactivationRequestOutcome.LATE


def test_deactivation_survives_episode_close_and_governs_next_occurrence() -> None:
    started, ids, alarm = _start(approval_required=False)
    deactivated_at = NOW + timedelta(minutes=1)
    until = deactivated_at + timedelta(hours=1)
    deactivated = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=deactivated_at),),
        at=deactivated_at,
        actions=(
            management_action(
                'risk',
                at=deactivated_at,
                deactivation_until=until,
            ),
        ),
        ids=ids,
    )
    closed_at = deactivated_at + timedelta(minutes=1)
    closed = _reduce(
        deactivated.state,
        (alarm,),
        (physical('risk', AlarmStatus.INACTIVE, at=closed_at),),
        at=closed_at,
        ids=ids,
    )
    dormant = closed.state.get(identity('risk'))
    assert closed.state.episode is None
    assert dormant is not None and dormant.occurrence is None
    assert dormant.deactivation_effect is not None

    reopened_at = closed_at + timedelta(minutes=1)
    reopened = _reduce(
        closed.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=reopened_at),),
        at=reopened_at,
        ids=ids,
    )
    runtime = reopened.state.get(identity('risk'))
    assert runtime is not None and runtime.occurrence is not None
    assert runtime.occurrence.occurrence_id == 'O2'
    assert runtime.deactivation_effect is not None
    assert reopened.priority_resolution is not None
    assert reopened.priority_resolution.alarms[0].disposition is PriorityDisposition.DEACTIVATED


def test_deactivation_expiry_before_reappearance_keeps_original_management_deadline() -> None:
    started, ids, alarm = _start(approval_required=False)
    managed_at = NOW + timedelta(minutes=1)
    deactivation_until = managed_at + timedelta(minutes=2)
    managed = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=managed_at),),
        at=managed_at,
        actions=(
            management_action(
                'risk',
                at=managed_at,
                deactivation_until=deactivation_until,
            ),
        ),
        ids=ids,
        reappearance_seconds=300,
    )
    at = deactivation_until
    expired = _reduce(
        managed.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=at),),
        at=at,
        ids=ids,
        reappearance_seconds=300,
    )
    runtime = expired.state.get(identity('risk'))
    assert runtime is not None
    assert runtime.deactivation_effect is None
    assert runtime.management_effect is not None
    assert runtime.management_effect.reappearance_due_at == managed_at + timedelta(minutes=5)


def test_deactivation_dominates_reappearance_at_due_time() -> None:
    started, ids, alarm = _start(approval_required=False)
    managed_at = NOW + timedelta(minutes=1)
    until = managed_at + timedelta(minutes=10)
    managed = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=managed_at),),
        at=managed_at,
        actions=(management_action('risk', at=managed_at, deactivation_until=until),),
        ids=ids,
        reappearance_seconds=300,
    )
    due = managed_at + timedelta(minutes=5)
    decision = _reduce(
        managed.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=due),),
        at=due,
        ids=ids,
        reappearance_seconds=300,
    )
    runtime = decision.state.get(identity('risk'))
    assert runtime is not None
    assert runtime.management_effect is None
    assert runtime.management_cycle == 1
    assert decision.reappearance_changes == ()
    assert decision.priority_resolution is not None
    assert decision.priority_resolution.alarms[0].disposition is PriorityDisposition.DEACTIVATED


def test_after_deactivation_expires_active_alarm_returns_to_priority() -> None:
    started, ids, alarm = _start(approval_required=False)
    at = NOW + timedelta(minutes=1)
    until = at + timedelta(minutes=2)
    deactivated = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=at),),
        at=at,
        actions=(management_action('risk', at=at, deactivation_until=until),),
        ids=ids,
        reappearance_seconds=30,
    )
    expired = _reduce(
        deactivated.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=until),),
        at=until,
        ids=ids,
        reappearance_seconds=30,
    )
    runtime = expired.state.get(identity('risk'))
    assert runtime is not None and runtime.deactivation_effect is None
    assert expired.priority_resolution is not None
    assert expired.priority_resolution.alarms[0].disposition is PriorityDisposition.PREDOMINANT


def test_deactivated_alarm_continues_c2_routing_progress() -> None:
    alarm = plan(
        'risk',
        destinations=(RoutingDestination('tool-b', 900),),
        deactivation_approval_required=False,
    )
    started, ids, _ = _start(planned=alarm)
    at = NOW + timedelta(minutes=1)
    deactivated = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=at),),
        at=at,
        actions=(
            management_action(
                'risk',
                at=at,
                deactivation_until=at + timedelta(hours=1),
            ),
        ),
        ids=ids,
    )
    due = NOW + timedelta(minutes=15)
    routed = _reduce(
        deactivated.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=due),),
        at=due,
        ids=ids,
    )
    runtime = routed.state.get(identity('risk'))
    assert runtime is not None
    assert ToolAssignment('tool-b', due) in runtime.assignments
    assert routed.priority_resolution is not None
    assert routed.priority_resolution.alarms[0].disposition is PriorityDisposition.DEACTIVATED


def test_deactivated_higher_priority_alarm_is_not_predominant() -> None:
    impact = plan(
        'impact',
        kind=AlarmKind.IMPACT,
        priority_order=1,
        deactivation_approval_required=False,
    )
    risk = plan('risk', priority_order=2)
    ids = Ids()
    started = _reduce(
        GroupLifecycleState(priority_group='mill-feed'),
        (impact, risk),
        (
            physical('impact', AlarmStatus.ACTIVE),
            physical('risk', AlarmStatus.ACTIVE),
        ),
        ids=ids,
    )
    at = NOW + timedelta(minutes=1)
    decision = _reduce(
        started.state,
        (impact, risk),
        (
            physical('impact', AlarmStatus.ACTIVE, at=at),
            physical('risk', AlarmStatus.ACTIVE, at=at),
        ),
        at=at,
        actions=(
            management_action(
                'impact',
                occurrence_id='O1',
                at=at,
                deactivation_until=at + timedelta(hours=1),
            ),
        ),
        ids=ids,
    )
    assert decision.priority_resolution is not None
    dispositions = {
        item.alarm_identity: item.disposition for item in decision.priority_resolution.alarms
    }
    assert dispositions[identity('impact')] is PriorityDisposition.DEACTIVATED
    assert dispositions[identity('risk')] is PriorityDisposition.CASCADE_SUPPRESSED


def test_structural_reset_preserves_active_deactivation_effect() -> None:
    started, ids, alarm = _start(approval_required=False)
    at = NOW + timedelta(minutes=1)
    deactivated = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=at),),
        at=at,
        actions=(
            management_action(
                'risk',
                at=at,
                deactivation_until=at + timedelta(hours=1),
            ),
        ),
        ids=ids,
    )
    reset_at = at + timedelta(minutes=1)
    reset = reset_group_for_reconfiguration(deactivated.state, effective_at=reset_at)
    runtime = reset.state.get(identity('risk'))
    assert runtime is not None and runtime.occurrence is None
    assert runtime.deactivation_effect is not None
    assert runtime.deactivation_effect.effective_until == at + timedelta(hours=1)
    assert reset.deactivation_effect_changes == ()


def test_approved_decision_after_request_window_is_expired() -> None:
    started, ids, alarm = _start(approval_required=True)
    requested_at = NOW - timedelta(minutes=10)
    pending = deactivation_request(
        requested_at=requested_at,
        effective_until=NOW - timedelta(minutes=1),
    )
    decision = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE),),
        pending_requests=(pending,),
        decisions=(deactivation_decision(at=NOW),),
        ids=ids,
    )
    assert decision.deactivation_decision_results[0].outcome is DeactivationDecisionOutcome.EXPIRED


def test_pending_approval_keeps_policy_captured_by_durable_request() -> None:
    started, ids, _ = _start(approval_required=True)
    pending = deactivation_request(requested_at=NOW - timedelta(minutes=1))
    direct_plan = plan('risk', deactivation_approval_required=False)
    decision = _reduce(
        started.state,
        (direct_plan,),
        (physical('risk', AlarmStatus.ACTIVE),),
        pending_requests=(pending,),
        decisions=(deactivation_decision(at=NOW),),
        ids=ids,
    )
    assert decision.deactivation_decision_results[0].outcome is DeactivationDecisionOutcome.APPLIED
    assert decision.state.get(identity('risk')).deactivation_effect is not None


def test_decision_and_request_can_resolve_in_same_cycle_when_request_identity_matches() -> None:
    started, ids, alarm = _start(approval_required=True)
    at = NOW + timedelta(minutes=1)
    decision = _reduce(
        started.state,
        (alarm,),
        (physical('risk', AlarmStatus.ACTIVE, at=at),),
        at=at,
        actions=(
            management_action(
                'risk',
                at=at,
                deactivation_until=at + timedelta(hours=1),
            ),
        ),
        decisions=(deactivation_decision(request_id='DR1', at=at),),
        ids=ids,
    )
    assert (
        decision.deactivation_request_results[0].outcome
        is DeactivationRequestOutcome.PENDING_APPROVAL
    )
    assert decision.deactivation_decision_results[0].outcome is DeactivationDecisionOutcome.APPLIED
    assert decision.state.get(identity('risk')).deactivation_effect is not None


def test_duplicate_pending_requests_for_same_alarm_are_rejected() -> None:
    started, ids, alarm = _start(approval_required=True)
    with pytest.raises(AlarmContractError, match='only one pending deactivation request'):
        _reduce(
            started.state,
            (alarm,),
            (physical('risk', AlarmStatus.ACTIVE),),
            pending_requests=(
                deactivation_request(request_id='DR1'),
                deactivation_request(request_id='DR2', management_input_id='M2'),
            ),
            ids=ids,
        )
