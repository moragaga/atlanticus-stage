from datetime import timedelta

import pytest

from ada.alarms.core import (
    AlarmContractError,
    AlarmKind,
    AlarmLifecycleError,
    AlarmStatus,
    ConfigurationClosure,
    Criticality,
    EpisodeChangeKind,
    EpisodeClosureReason,
    GroupLifecycleState,
    OccurrenceChangeKind,
    OccurrenceClosureReason,
    TechnicalHoldChangeKind,
    reduce_group_cycle,
    reset_group_for_reconfiguration,
)

from .support import NOW, Ids, error, identity, physical, plan


def _reduce(
    state: GroupLifecycleState,
    plans,
    evaluations,
    *,
    at=NOW,
    closures=(),
    ids: Ids | None = None,
):
    generated = ids or Ids()
    return reduce_group_cycle(
        state,
        cycle_at=at,
        planned_alarms=plans,
        evaluations=evaluations,
        configuration_closures=closures,
        occurrence_id_factory=generated.new_occurrence,
        episode_id_factory=generated.new_episode,
    )


def _start_risk(ids: Ids | None = None):
    generated = ids or Ids()
    decision = _reduce(
        GroupLifecycleState(priority_group='mill-feed'),
        [plan('risk')],
        [physical('risk', AlarmStatus.ACTIVE)],
        ids=generated,
    )
    return decision, generated


def test_inactive_to_active_opens_occurrence_and_episode() -> None:
    decision, _ = _start_risk()
    assert decision.has_lifecycle_change
    assert decision.state.episode is not None
    assert decision.state.episode.episode_id == 'E1'
    runtime = decision.state.get(identity('risk'))
    assert runtime is not None and runtime.occurrence is not None
    assert runtime.occurrence.occurrence_id == 'O1'
    assert runtime.occurrence.started_at == NOW
    assert [change.kind for change in decision.occurrence_changes] == [OccurrenceChangeKind.STARTED]
    assert decision.episode_changes
    assert decision.episode_changes[0].kind is EpisodeChangeKind.STARTED


def test_active_to_active_without_due_work_is_noop() -> None:
    started, ids = _start_risk()
    later = NOW + timedelta(seconds=10)
    decision = _reduce(
        started.state,
        [plan('risk')],
        [physical('risk', AlarmStatus.ACTIVE, at=later)],
        at=later,
        ids=ids,
    )
    assert not decision.has_lifecycle_change
    assert decision.state == started.state


def test_error_without_occurrence_never_creates_occurrence() -> None:
    decision = _reduce(
        GroupLifecycleState(priority_group='mill-feed'),
        [plan('risk')],
        [error('risk')],
    )
    assert not decision.has_lifecycle_change
    assert decision.state.episode is None
    assert decision.state.alarms == ()


def test_error_with_occurrence_starts_technical_hold_for_300_seconds() -> None:
    started, ids = _start_risk()
    at = NOW + timedelta(seconds=10)
    decision = _reduce(
        started.state,
        [plan('risk')],
        [error('risk', at=at)],
        at=at,
        ids=ids,
    )
    runtime = decision.state.get(identity('risk'))
    assert runtime is not None and runtime.technical_hold is not None
    assert runtime.technical_hold.started_at == at
    assert runtime.technical_hold.due_at == at + timedelta(seconds=300)
    assert decision.technical_hold_changes[0].kind is TechnicalHoldChangeKind.STARTED


def test_repeated_error_before_deadline_keeps_same_hold_without_commit() -> None:
    started, ids = _start_risk()
    first_error_at = NOW + timedelta(seconds=10)
    held = _reduce(
        started.state,
        [plan('risk')],
        [error('risk', at=first_error_at)],
        at=first_error_at,
        ids=ids,
    )
    again_at = first_error_at + timedelta(seconds=100)
    decision = _reduce(
        held.state,
        [plan('risk')],
        [error('risk', at=again_at)],
        at=again_at,
        ids=ids,
    )
    assert not decision.has_lifecycle_change
    assert decision.state == held.state


def test_error_at_technical_hold_deadline_closes_non_naturally() -> None:
    started, ids = _start_risk()
    error_at = NOW + timedelta(seconds=10)
    held = _reduce(
        started.state,
        [plan('risk')],
        [error('risk', at=error_at)],
        at=error_at,
        ids=ids,
    )
    due = error_at + timedelta(seconds=300)
    decision = _reduce(
        held.state,
        [plan('risk')],
        [error('risk', at=due)],
        at=due,
        ids=ids,
    )
    closed = decision.occurrence_changes[0].occurrence
    assert closed.closure_reason is OccurrenceClosureReason.TECHNICAL_HOLD_EXPIRED
    assert closed.ended_at == due
    assert decision.episode_changes
    assert (
        decision.episode_changes[0].episode.closure_reason
        is EpisodeClosureReason.TECHNICAL_UNCERTAINTY
    )
    assert decision.state.episode is None


def test_valid_active_at_exact_th_deadline_wins_over_expiry() -> None:
    started, ids = _start_risk()
    error_at = NOW + timedelta(seconds=10)
    held = _reduce(
        started.state,
        [plan('risk')],
        [error('risk', at=error_at)],
        at=error_at,
        ids=ids,
    )
    due = error_at + timedelta(seconds=300)
    decision = _reduce(
        held.state,
        [plan('risk')],
        [physical('risk', AlarmStatus.ACTIVE, at=due)],
        at=due,
        ids=ids,
    )
    runtime = decision.state.get(identity('risk'))
    assert runtime is not None
    assert runtime.occurrence is not None and runtime.occurrence.occurrence_id == 'O1'
    assert runtime.technical_hold is None
    assert decision.occurrence_changes == ()
    assert decision.technical_hold_changes[0].kind is TechnicalHoldChangeKind.CLEARED


def test_valid_inactive_at_exact_th_deadline_closes_by_normalization() -> None:
    started, ids = _start_risk()
    error_at = NOW + timedelta(seconds=10)
    held = _reduce(
        started.state,
        [plan('risk')],
        [error('risk', at=error_at)],
        at=error_at,
        ids=ids,
    )
    due = error_at + timedelta(seconds=300)
    decision = _reduce(
        held.state,
        [plan('risk')],
        [physical('risk', AlarmStatus.INACTIVE, at=due)],
        at=due,
        ids=ids,
    )
    assert (
        decision.occurrence_changes[0].occurrence.closure_reason
        is OccurrenceClosureReason.CONDITION_NORMALIZED
    )
    assert decision.episode_changes
    assert (
        decision.episode_changes[0].episode.closure_reason
        is EpisodeClosureReason.CONDITION_NORMALIZED
    )


def test_reactivation_after_full_normalization_uses_new_occurrence_and_episode() -> None:
    ids = Ids()
    started, _ = _start_risk(ids)
    closed_at = NOW + timedelta(minutes=1)
    closed = _reduce(
        started.state,
        [plan('risk')],
        [physical('risk', AlarmStatus.INACTIVE, at=closed_at)],
        at=closed_at,
        ids=ids,
    )
    reopened_at = closed_at + timedelta(seconds=1)
    reopened = _reduce(
        closed.state,
        [plan('risk')],
        [physical('risk', AlarmStatus.ACTIVE, at=reopened_at)],
        at=reopened_at,
        ids=ids,
    )
    runtime = reopened.state.get(identity('risk'))
    assert runtime is not None and runtime.occurrence is not None
    assert runtime.occurrence.occurrence_id == 'O2'
    assert reopened.state.episode is not None
    assert reopened.state.episode.episode_id == 'E2'


def test_risk_closes_while_impact_starts_same_cycle_keeps_episode() -> None:
    ids = Ids()
    started, _ = _start_risk(ids)
    at = NOW + timedelta(minutes=1)
    decision = _reduce(
        started.state,
        [
            plan('risk', kind=AlarmKind.RISK, priority_order=2),
            plan(
                'impact',
                kind=AlarmKind.IMPACT,
                criticality=Criticality.C1,
                priority_order=1,
            ),
        ],
        [
            physical('risk', AlarmStatus.INACTIVE, at=at),
            physical('impact', AlarmStatus.ACTIVE, at=at),
        ],
        at=at,
        ids=ids,
    )
    assert decision.state.episode is not None
    assert decision.state.episode.episode_id == 'E1'
    assert decision.episode_changes == ()
    impact = decision.state.get(identity('impact'))
    assert impact is not None and impact.occurrence is not None
    assert impact.occurrence.episode_id == 'E1'


def test_two_simultaneous_new_occurrences_share_one_episode() -> None:
    decision = _reduce(
        GroupLifecycleState(priority_group='mill-feed'),
        [
            plan('risk', priority_order=2),
            plan('impact', kind=AlarmKind.IMPACT, priority_order=1),
        ],
        [
            physical('risk', AlarmStatus.ACTIVE),
            physical('impact', AlarmStatus.ACTIVE),
        ],
    )
    assert decision.state.episode is not None
    episode_id = decision.state.episode.episode_id
    assert {alarm.occurrence.episode_id for alarm in decision.state.alarms if alarm.occurrence} == {
        episode_id
    }


def test_configuration_disabled_closes_open_occurrence_without_normalization() -> None:
    started, ids = _start_risk()
    at = NOW + timedelta(minutes=2)
    closure = ConfigurationClosure(
        alarm_identity=identity('risk'),
        reason=OccurrenceClosureReason.CONFIGURATION_DISABLED,
        effective_at=at,
    )
    decision = _reduce(started.state, [], [], at=at, closures=[closure], ids=ids)
    assert (
        decision.occurrence_changes[0].occurrence.closure_reason
        is OccurrenceClosureReason.CONFIGURATION_DISABLED
    )
    assert decision.episode_changes
    assert (
        decision.episode_changes[0].episode.closure_reason
        is EpisodeClosureReason.CONFIGURATION_TERMINATED
    )


def test_configuration_removed_does_not_close_episode_if_other_occurrence_continues() -> None:
    ids = Ids()
    started = _reduce(
        GroupLifecycleState(priority_group='mill-feed'),
        [plan('risk'), plan('impact', kind=AlarmKind.IMPACT, priority_order=1)],
        [physical('risk', AlarmStatus.ACTIVE), physical('impact', AlarmStatus.ACTIVE)],
        ids=ids,
    )
    at = NOW + timedelta(minutes=2)
    closure = ConfigurationClosure(
        alarm_identity=identity('risk'),
        reason=OccurrenceClosureReason.CONFIGURATION_REMOVED,
        effective_at=at,
    )
    decision = _reduce(
        started.state,
        [plan('impact', kind=AlarmKind.IMPACT, priority_order=1)],
        [physical('impact', AlarmStatus.ACTIVE, at=at)],
        at=at,
        closures=[closure],
        ids=ids,
    )
    assert decision.state.episode is not None
    assert decision.state.episode.episode_id == 'E1'
    assert decision.episode_changes == ()


def test_normalization_wins_over_configuration_close_at_same_effective_time() -> None:
    started, ids = _start_risk()
    at = NOW + timedelta(minutes=1)
    closure = ConfigurationClosure(
        alarm_identity=identity('risk'),
        reason=OccurrenceClosureReason.CONFIGURATION_DISABLED,
        effective_at=at,
    )
    decision = _reduce(
        started.state,
        [],
        [physical('risk', AlarmStatus.INACTIVE, at=at)],
        at=at,
        closures=[closure],
        ids=ids,
    )
    assert (
        decision.occurrence_changes[0].occurrence.closure_reason
        is OccurrenceClosureReason.CONDITION_NORMALIZED
    )
    assert decision.episode_changes
    assert (
        decision.episode_changes[0].episode.closure_reason
        is EpisodeClosureReason.CONDITION_NORMALIZED
    )


def test_structural_reset_closes_whole_group_without_reinterpreting_history() -> None:
    ids = Ids()
    started = _reduce(
        GroupLifecycleState(priority_group='mill-feed'),
        [plan('risk'), plan('impact', kind=AlarmKind.IMPACT, priority_order=1)],
        [physical('risk', AlarmStatus.ACTIVE), physical('impact', AlarmStatus.ACTIVE)],
        ids=ids,
    )
    at = NOW + timedelta(minutes=5)
    decision = reset_group_for_reconfiguration(started.state, effective_at=at)
    assert decision.state.alarms == ()
    assert decision.state.episode is None
    assert {change.occurrence.closure_reason for change in decision.occurrence_changes} == {
        OccurrenceClosureReason.CONFIGURATION_RECONFIGURED
    }
    assert decision.episode_changes
    assert (
        decision.episode_changes[0].episode.closure_reason
        is EpisodeClosureReason.CONFIGURATION_TERMINATED
    )


def test_missing_evaluation_is_contract_error_not_inactive() -> None:
    with pytest.raises(AlarmContractError, match='missing_evaluation'):
        _reduce(
            GroupLifecycleState(priority_group='mill-feed'),
            [plan('risk')],
            [],
        )


def test_duplicate_evaluation_is_contract_error() -> None:
    with pytest.raises(AlarmContractError, match='duplicate_evaluation'):
        _reduce(
            GroupLifecycleState(priority_group='mill-feed'),
            [plan('risk')],
            [
                physical('risk', AlarmStatus.ACTIVE),
                physical('risk', AlarmStatus.ACTIVE),
            ],
        )


def test_unplanned_evaluation_is_contract_error() -> None:
    with pytest.raises(AlarmContractError, match='unexpected_evaluation'):
        _reduce(
            GroupLifecycleState(priority_group='mill-feed'),
            [],
            [physical('risk', AlarmStatus.ACTIVE)],
        )


def test_overdue_technical_hold_closes_before_fresh_active_and_opens_new_episode() -> None:
    ids = Ids()
    started, _ = _start_risk(ids)
    error_at = NOW + timedelta(seconds=10)
    held = _reduce(
        started.state,
        [plan('risk')],
        [error('risk', at=error_at)],
        at=error_at,
        ids=ids,
    )
    due = error_at + timedelta(seconds=300)
    recovered_at = due + timedelta(seconds=20)
    decision = _reduce(
        held.state,
        [plan('risk')],
        [physical('risk', AlarmStatus.ACTIVE, at=recovered_at)],
        at=recovered_at,
        ids=ids,
    )

    assert [change.kind for change in decision.occurrence_changes] == [
        OccurrenceChangeKind.CLOSED,
        OccurrenceChangeKind.STARTED,
    ]
    assert decision.occurrence_changes[0].occurrence.occurrence_id == 'O1'
    assert decision.occurrence_changes[0].occurrence.ended_at == due
    assert (
        decision.occurrence_changes[0].occurrence.closure_reason
        is OccurrenceClosureReason.TECHNICAL_HOLD_EXPIRED
    )
    assert decision.occurrence_changes[1].occurrence.occurrence_id == 'O2'
    assert decision.occurrence_changes[1].occurrence.started_at == recovered_at
    assert [change.kind for change in decision.episode_changes] == [
        EpisodeChangeKind.CLOSED,
        EpisodeChangeKind.STARTED,
    ]
    assert decision.episode_changes[0].episode.episode_id == 'E1'
    assert decision.episode_changes[0].episode.ended_at == due
    assert (
        decision.episode_changes[0].episode.closure_reason
        is EpisodeClosureReason.TECHNICAL_UNCERTAINTY
    )
    assert decision.episode_changes[1].episode.episode_id == 'E2'
    runtime = decision.state.get(identity('risk'))
    assert runtime is not None and runtime.occurrence is not None
    assert runtime.occurrence.occurrence_id == 'O2'
    assert runtime.occurrence.episode_id == 'E2'


def test_overdue_technical_hold_with_other_active_keeps_episode_for_new_occurrence() -> None:
    ids = Ids()
    started = _reduce(
        GroupLifecycleState(priority_group='mill-feed'),
        [
            plan('risk', priority_order=2),
            plan('impact', kind=AlarmKind.IMPACT, priority_order=1),
        ],
        [
            physical('risk', AlarmStatus.ACTIVE),
            physical('impact', AlarmStatus.ACTIVE),
        ],
        ids=ids,
    )
    error_at = NOW + timedelta(seconds=10)
    held = _reduce(
        started.state,
        [
            plan('risk', priority_order=2),
            plan('impact', kind=AlarmKind.IMPACT, priority_order=1),
        ],
        [
            physical('risk', AlarmStatus.ACTIVE, at=error_at),
            error('impact', at=error_at),
        ],
        at=error_at,
        ids=ids,
    )
    due = error_at + timedelta(seconds=300)
    recovered_at = due + timedelta(seconds=5)
    decision = _reduce(
        held.state,
        [
            plan('risk', priority_order=2),
            plan('impact', kind=AlarmKind.IMPACT, priority_order=1),
        ],
        [
            physical('risk', AlarmStatus.ACTIVE, at=recovered_at),
            physical('impact', AlarmStatus.ACTIVE, at=recovered_at),
        ],
        at=recovered_at,
        ids=ids,
    )

    assert decision.episode_changes == ()
    assert decision.state.episode is not None
    assert decision.state.episode.episode_id == 'E1'
    impact = decision.state.get(identity('impact'))
    assert impact is not None and impact.occurrence is not None
    assert impact.occurrence.occurrence_id == 'O3'
    assert impact.occurrence.episode_id == 'E1'
    closed = [
        change
        for change in decision.occurrence_changes
        if change.kind is OccurrenceChangeKind.CLOSED
    ]
    assert len(closed) == 1
    assert closed[0].occurrence.ended_at == due
    assert closed[0].occurrence.closure_reason is OccurrenceClosureReason.TECHNICAL_HOLD_EXPIRED


def test_occurrence_factory_cannot_reuse_current_identifier_after_overdue_expiry() -> None:
    started, _ = _start_risk()
    error_at = NOW + timedelta(seconds=10)
    held = _reduce(
        started.state,
        [plan('risk')],
        [error('risk', at=error_at)],
        at=error_at,
    )
    recovered_at = error_at + timedelta(seconds=301)

    with pytest.raises(AlarmLifecycleError, match='duplicate identifier'):
        reduce_group_cycle(
            held.state,
            cycle_at=recovered_at,
            planned_alarms=[plan('risk')],
            evaluations=[physical('risk', AlarmStatus.ACTIVE, at=recovered_at)],
            occurrence_id_factory=lambda _identity, _at: 'O1',
            episode_id_factory=lambda _group, _at: 'E2',
        )


def test_execution_plan_rejects_duplicate_priority_order_within_group() -> None:
    with pytest.raises(AlarmContractError, match='duplicate priority_order'):
        _reduce(
            GroupLifecycleState(priority_group='mill-feed'),
            [
                plan('risk', priority_order=1),
                plan('impact', kind=AlarmKind.IMPACT, priority_order=1),
            ],
            [
                physical('risk', AlarmStatus.INACTIVE),
                physical('impact', AlarmStatus.INACTIVE),
            ],
        )


def test_execution_plan_requires_all_impacts_above_all_risks() -> None:
    with pytest.raises(AlarmContractError, match='IMPACT priority_order values must be lower'):
        _reduce(
            GroupLifecycleState(priority_group='mill-feed'),
            [
                plan('risk', priority_order=1),
                plan('impact', kind=AlarmKind.IMPACT, priority_order=2),
            ],
            [
                physical('risk', AlarmStatus.INACTIVE),
                physical('impact', AlarmStatus.INACTIVE),
            ],
        )
