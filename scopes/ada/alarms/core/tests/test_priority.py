import pytest

from ada.alarms.core import (
    AlarmContractError,
    AlarmKind,
    AlarmStatus,
    CascadeSuppression,
    GroupLifecycleState,
    PriorityDisposition,
    reduce_group_cycle,
    resolve_group_priority,
)

from .support import NOW, Ids, identity, physical, plan


def _start(plans):
    ids = Ids()
    decision = reduce_group_cycle(
        GroupLifecycleState(priority_group='mill-feed'),
        cycle_at=NOW,
        planned_alarms=plans,
        evaluations=tuple(
            physical(planned.identity.alarm_key, AlarmStatus.ACTIVE) for planned in plans
        ),
        occurrence_id_factory=ids.new_occurrence,
        episode_id_factory=ids.new_episode,
    )
    return decision


def _dispositions(resolution):
    return {item.alarm_identity: item for item in resolution.alarms}


def test_higher_priority_impact_predominates_over_active_risk() -> None:
    plans = (
        plan('impact', kind=AlarmKind.IMPACT, priority_order=1),
        plan('risk', kind=AlarmKind.RISK, priority_order=2),
    )
    decision = _start(plans)
    resolution = decision.priority_resolution
    assert resolution is not None
    assert resolution.predominant_alarm_identity == identity('impact')
    dispositions = _dispositions(resolution)
    assert dispositions[identity('impact')].disposition is PriorityDisposition.PREDOMINANT
    assert dispositions[identity('risk')].disposition is PriorityDisposition.ECLIPSED
    assert dispositions[identity('risk')].blocking_alarm_identities == (identity('impact'),)


def test_priority_order_resolves_multiple_impacts_before_risks() -> None:
    plans = (
        plan('impact-a', kind=AlarmKind.IMPACT, priority_order=1),
        plan('impact-b', kind=AlarmKind.IMPACT, priority_order=2),
        plan('risk', kind=AlarmKind.RISK, priority_order=3),
    )
    decision = _start(plans)
    resolution = decision.priority_resolution
    assert resolution is not None
    assert resolution.predominant_alarm_identity == identity('impact-a')


def test_shadow_alarm_never_predominates_or_blocks_operational_alarm() -> None:
    plans = (
        plan(
            'impact',
            kind=AlarmKind.IMPACT,
            priority_order=1,
            delivery_enabled=False,
        ),
        plan('risk', kind=AlarmKind.RISK, priority_order=2),
    )
    decision = _start(plans)
    resolution = decision.priority_resolution
    assert resolution is not None
    assert resolution.predominant_alarm_identity == identity('risk')
    dispositions = _dispositions(resolution)
    assert dispositions[identity('impact')].disposition is PriorityDisposition.SHADOW
    assert dispositions[identity('risk')].disposition is PriorityDisposition.PREDOMINANT


def test_cascade_suppressed_risk_can_leave_group_without_predominant_alarm() -> None:
    plans = (plan('risk'),)
    decision = _start(plans)
    suppression = CascadeSuppression(
        source_alarm_identity=identity('impact'),
        source_occurrence_id='I-old',
        management_effect_id='ME1',
        target_alarm_identity=identity('risk'),
    )
    resolution = resolve_group_priority(
        decision.state,
        planned_alarms=plans,
        cascade_suppressions=(suppression,),
    )
    assert resolution.predominant_alarm_identity is None
    item = resolution.alarms[0]
    assert item.disposition is PriorityDisposition.CASCADE_SUPPRESSED
    assert item.blocking_alarm_identities == (identity('impact'),)


def test_cascade_suppression_releases_next_operational_candidate() -> None:
    plans = (
        plan('risk-a', kind=AlarmKind.RISK, priority_order=2),
        plan('risk-b', kind=AlarmKind.RISK, priority_order=3),
    )
    decision = _start(plans)
    suppression = CascadeSuppression(
        source_alarm_identity=identity('impact'),
        source_occurrence_id='I-old',
        management_effect_id='ME1',
        target_alarm_identity=identity('risk-a'),
    )
    resolution = resolve_group_priority(
        decision.state,
        planned_alarms=plans,
        cascade_suppressions=(suppression,),
    )
    assert resolution.predominant_alarm_identity == identity('risk-b')
    dispositions = _dispositions(resolution)
    assert dispositions[identity('risk-a')].disposition is PriorityDisposition.CASCADE_SUPPRESSED
    assert dispositions[identity('risk-b')].disposition is PriorityDisposition.PREDOMINANT


def test_multiple_cascade_sources_are_reported_deterministically() -> None:
    plans = (plan('risk'),)
    decision = _start(plans)
    suppressions = (
        CascadeSuppression(identity('impact-b'), 'I2', 'ME2', identity('risk')),
        CascadeSuppression(identity('impact-a'), 'I1', 'ME1', identity('risk')),
    )
    resolution = resolve_group_priority(
        decision.state,
        planned_alarms=plans,
        cascade_suppressions=suppressions,
    )
    assert resolution.alarms[0].blocking_alarm_identities == (
        identity('impact-a'),
        identity('impact-b'),
    )


def test_management_only_alarm_is_not_a_priority_candidate() -> None:
    plans = (plan('risk'),)
    decision = _start(plans)
    runtime = decision.state.get(identity('risk'))
    assert runtime is not None
    state = GroupLifecycleState(
        priority_group='mill-feed',
        episode=None,
        alarms=(),
    )
    resolution = resolve_group_priority(state, planned_alarms=plans)
    assert resolution.predominant_alarm_identity is None
    assert resolution.alarms == ()


def test_priority_resolution_rejects_duplicate_priority_order() -> None:
    plans = (
        plan('risk-a', priority_order=2),
        plan('risk-b', priority_order=2),
    )
    with pytest.raises(AlarmContractError, match='duplicate priority_order'):
        resolve_group_priority(
            GroupLifecycleState(priority_group='mill-feed'),
            planned_alarms=plans,
        )


def test_priority_resolution_is_derived_and_does_not_change_hot_state() -> None:
    plans = (
        plan('impact', kind=AlarmKind.IMPACT, priority_order=1),
        plan('risk', kind=AlarmKind.RISK, priority_order=2),
    )
    decision = _start(plans)
    before = decision.state
    resolution = resolve_group_priority(before, planned_alarms=plans)
    assert decision.state == before
    assert resolution.predominant_alarm_identity == identity('impact')
