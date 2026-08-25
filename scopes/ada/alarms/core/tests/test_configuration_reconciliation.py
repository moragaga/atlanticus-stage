from dataclasses import replace
from datetime import timedelta

import pytest

from ada.alarms.core import (
    AlarmContractError,
    AlarmKind,
    AlarmStatus,
    AssignmentChangeKind,
    ConfigurationClosure,
    Criticality,
    EpisodeClosureReason,
    GroupLifecycleState,
    OccurrenceClosureReason,
    RoutingDestination,
    reconcile_group_configuration,
    reduce_group_cycle,
)

from .support import NOW, Ids, management_action, physical, plan


def _reduce(state, plans, evaluations, *, at=NOW, actions=(), ids=None):
    generated = ids or Ids()
    return reduce_group_cycle(
        state,
        cycle_at=at,
        planned_alarms=plans,
        evaluations=evaluations,
        occurrence_id_factory=generated.new_occurrence,
        episode_id_factory=generated.new_episode,
        management_actions=actions,
        management_effect_id_factory=generated.new_management_effect,
        reappearance_due_at_resolver=lambda action: action.source_created_at + timedelta(minutes=5),
        deactivation_request_id_factory=generated.new_deactivation_request,
        deactivation_effect_id_factory=generated.new_deactivation_effect,
    )


def test_reconcile_configuration_closes_disabled_occurrence_and_keeps_other_episode() -> None:
    ids = Ids()
    risk = plan('risk', priority_order=2)
    impact = plan('impact', kind=AlarmKind.IMPACT, priority_order=1)
    started = _reduce(
        GroupLifecycleState(priority_group='mill-feed'),
        (risk, impact),
        (
            physical('risk', AlarmStatus.ACTIVE),
            physical('impact', AlarmStatus.ACTIVE),
        ),
        ids=ids,
    )
    effective_at = NOW + timedelta(minutes=2)
    decision = reconcile_group_configuration(
        started.state,
        effective_at=effective_at,
        planned_alarms=(impact,),
        configuration_closures=(
            ConfigurationClosure(
                alarm_identity=risk.identity,
                reason=OccurrenceClosureReason.CONFIGURATION_DISABLED,
                effective_at=effective_at,
            ),
        ),
    )

    closed = decision.occurrence_changes[0].occurrence
    assert closed.alarm_identity == risk.identity
    assert closed.closure_reason is OccurrenceClosureReason.CONFIGURATION_DISABLED
    assert decision.state.get(risk.identity) is None
    assert decision.state.get(impact.identity).occurrence is not None
    assert decision.state.episode is not None
    assert decision.state.episode.episode_id == 'E1'
    assert decision.episode_changes == ()


def test_reconcile_c2_routing_reschedules_pending_from_original_occurrence_start() -> None:
    ids = Ids()
    source = plan('risk')
    started = _reduce(
        GroupLifecycleState(priority_group='mill-feed'),
        (source,),
        (physical('risk', AlarmStatus.ACTIVE),),
        ids=ids,
    )
    effective_at = NOW + timedelta(minutes=5)
    target = replace(
        source,
        routing=replace(
            source.routing,
            destinations=(RoutingDestination(tool_key='tool-b', delay_seconds=1800),),
        ),
    )

    decision = reconcile_group_configuration(
        started.state,
        effective_at=effective_at,
        planned_alarms=(target,),
    )

    runtime = decision.state.get(source.identity)
    assert runtime is not None
    assert runtime.pending_assignments[0].due_at == NOW + timedelta(minutes=30)
    assert len(decision.assignment_changes) == 1
    change = decision.assignment_changes[0]
    assert change.kind is AssignmentChangeKind.RESCHEDULED
    assert change.due_at == NOW + timedelta(minutes=30)


def test_reconcile_structural_reset_closes_continuity_and_preserves_active_deactivation() -> None:
    ids = Ids()
    source = plan('risk', deactivation_approval_required=False)
    started = _reduce(
        GroupLifecycleState(priority_group='mill-feed'),
        (source,),
        (physical('risk', AlarmStatus.ACTIVE),),
        ids=ids,
    )
    deactivated_at = NOW + timedelta(minutes=1)
    deactivated = _reduce(
        started.state,
        (source,),
        (physical('risk', AlarmStatus.ACTIVE, at=deactivated_at),),
        at=deactivated_at,
        actions=(
            management_action(
                'risk',
                at=deactivated_at,
                deactivation_until=deactivated_at + timedelta(hours=1),
            ),
        ),
        ids=ids,
    )
    target = replace(
        source,
        criticality=Criticality.C1,
        routing=replace(
            source.routing,
            destinations=(RoutingDestination(tool_key='tool-b'),),
        ),
    )
    effective_at = deactivated_at + timedelta(minutes=1)

    decision = reconcile_group_configuration(
        deactivated.state,
        effective_at=effective_at,
        planned_alarms=(target,),
        structural_reset=True,
    )

    assert decision.occurrence_changes[0].occurrence.closure_reason is (
        OccurrenceClosureReason.CONFIGURATION_RECONFIGURED
    )
    assert decision.episode_changes[0].episode.closure_reason is (
        EpisodeClosureReason.CONFIGURATION_TERMINATED
    )
    runtime = decision.state.get(source.identity)
    assert runtime is not None and runtime.occurrence is None
    assert runtime.deactivation_effect is not None
    assert decision.deactivation_effect_changes == ()


def test_reconcile_structural_reset_preserves_deactivation_for_non_executable_alarm() -> None:
    ids = Ids()
    source = plan('risk', deactivation_approval_required=False)
    started = _reduce(
        GroupLifecycleState(priority_group='mill-feed'),
        (source,),
        (physical('risk', AlarmStatus.ACTIVE),),
        ids=ids,
    )
    deactivated_at = NOW + timedelta(minutes=1)
    deactivated = _reduce(
        started.state,
        (source,),
        (physical('risk', AlarmStatus.ACTIVE, at=deactivated_at),),
        at=deactivated_at,
        actions=(
            management_action(
                'risk',
                at=deactivated_at,
                deactivation_until=deactivated_at + timedelta(hours=1),
            ),
        ),
        ids=ids,
    )
    effective_at = deactivated_at + timedelta(minutes=1)

    decision = reconcile_group_configuration(
        deactivated.state,
        effective_at=effective_at,
        planned_alarms=(),
        structural_reset=True,
    )

    runtime = decision.state.get(source.identity)
    assert runtime is not None
    assert runtime.occurrence is None
    assert runtime.deactivation_effect is not None
    assert runtime.deactivation_effect.effective_until == deactivated_at + timedelta(hours=1)
    assert decision.deactivation_effect_changes == ()


@pytest.mark.parametrize(
    'reason',
    (
        OccurrenceClosureReason.CONFIGURATION_DISABLED,
        OccurrenceClosureReason.CONFIGURATION_REMOVED,
    ),
)
def test_reconcile_configuration_closure_preserves_active_deactivation(reason) -> None:
    ids = Ids()
    source = plan('risk', deactivation_approval_required=False)
    started = _reduce(
        GroupLifecycleState(priority_group='mill-feed'),
        (source,),
        (physical('risk', AlarmStatus.ACTIVE),),
        ids=ids,
    )
    deactivated_at = NOW + timedelta(minutes=1)
    deactivated = _reduce(
        started.state,
        (source,),
        (physical('risk', AlarmStatus.ACTIVE, at=deactivated_at),),
        at=deactivated_at,
        actions=(
            management_action(
                'risk',
                at=deactivated_at,
                deactivation_until=deactivated_at + timedelta(hours=1),
            ),
        ),
        ids=ids,
    )
    effective_at = deactivated_at + timedelta(minutes=1)

    decision = reconcile_group_configuration(
        deactivated.state,
        effective_at=effective_at,
        planned_alarms=(),
        configuration_closures=(
            ConfigurationClosure(
                alarm_identity=source.identity,
                reason=reason,
                effective_at=effective_at,
            ),
        ),
    )

    runtime = decision.state.get(source.identity)
    assert runtime is not None
    assert runtime.occurrence is None
    assert runtime.management_effect is None
    assert runtime.deactivation_effect is not None
    assert runtime.deactivation_effect.effective_until == deactivated_at + timedelta(hours=1)
    assert decision.deactivation_effect_changes == ()
    assert decision.occurrence_changes[0].occurrence.closure_reason is reason
    assert decision.episode_changes[0].episode.closure_reason is (
        EpisodeClosureReason.CONFIGURATION_TERMINATED
    )


def test_reconcile_rejects_configuration_closure_for_executable_target_alarm() -> None:
    source = plan('risk')
    effective_at = NOW + timedelta(minutes=1)
    with pytest.raises(AlarmContractError, match='must not remain executable'):
        reconcile_group_configuration(
            GroupLifecycleState(priority_group='mill-feed'),
            effective_at=effective_at,
            planned_alarms=(source,),
            configuration_closures=(
                ConfigurationClosure(
                    alarm_identity=source.identity,
                    reason=OccurrenceClosureReason.CONFIGURATION_DISABLED,
                    effective_at=effective_at,
                ),
            ),
        )
