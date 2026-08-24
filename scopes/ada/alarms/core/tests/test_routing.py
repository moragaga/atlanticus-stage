from datetime import timedelta

from ada.alarms.core import (
    AlarmKind,
    AlarmStatus,
    AssignmentChangeKind,
    Criticality,
    GroupLifecycleState,
    PendingToolAssignment,
    RoutingDestination,
    ToolAssignment,
    reduce_group_cycle,
    resolve_alarm_routing,
)

from .support import NOW, Ids, physical, plan


def _start(planned, *, at=NOW):
    ids = Ids()
    decision = reduce_group_cycle(
        GroupLifecycleState(priority_group='mill-feed'),
        cycle_at=at,
        planned_alarms=(planned,),
        evaluations=(physical(planned.identity.alarm_key, AlarmStatus.ACTIVE, at=at),),
        occurrence_id_factory=ids.new_occurrence,
        episode_id_factory=ids.new_episode,
    )
    runtime = decision.state.get(planned.identity)
    assert runtime is not None
    return decision, runtime, ids


def test_c1_assigns_origin_and_destinations_immediately() -> None:
    planned = plan(
        'impact',
        kind=AlarmKind.IMPACT,
        criticality=Criticality.C1,
        priority_order=1,
        destinations=(RoutingDestination('tool-b'), RoutingDestination('tool-c')),
    )
    decision, runtime, _ = _start(planned)
    assert runtime.assignments == (
        ToolAssignment('tool-a', NOW),
        ToolAssignment('tool-b', NOW),
        ToolAssignment('tool-c', NOW),
    )
    assert runtime.pending_assignments == ()
    assert [change.kind for change in decision.assignment_changes] == [
        AssignmentChangeKind.ASSIGNED,
        AssignmentChangeKind.ASSIGNED,
        AssignmentChangeKind.ASSIGNED,
    ]


def test_c2_assigns_origin_and_schedules_absolute_destination_deadlines() -> None:
    planned = plan(
        destinations=(
            RoutingDestination('tool-b', 900),
            RoutingDestination('tool-c', 1800),
        )
    )
    _, runtime, _ = _start(planned)
    assert runtime.assignments == (ToolAssignment('tool-a', NOW),)
    assert runtime.pending_assignments == (
        PendingToolAssignment('tool-b', NOW + timedelta(minutes=15)),
        PendingToolAssignment('tool-c', NOW + timedelta(minutes=30)),
    )


def test_c3_routes_only_to_origin() -> None:
    planned = plan(criticality=Criticality.C3)
    _, runtime, _ = _start(planned)
    assert runtime.assignments == (ToolAssignment('tool-a', NOW),)
    assert runtime.pending_assignments == ()


def test_c2_pending_deadline_reaches_at_absolute_due_time() -> None:
    planned = plan(destinations=(RoutingDestination('tool-b', 900),))
    _, runtime, _ = _start(planned)
    due = NOW + timedelta(minutes=15)
    routed, changes = resolve_alarm_routing(planned, runtime, cycle_at=due)
    assert routed.assignments == (
        ToolAssignment('tool-a', NOW),
        ToolAssignment('tool-b', due),
    )
    assert routed.pending_assignments == ()
    assert changes[0].kind is AssignmentChangeKind.ASSIGNED
    assert changes[0].effective_at == due


def test_c2_shorter_timer_that_becomes_overdue_reaches_at_adoption_cycle() -> None:
    original = plan(destinations=(RoutingDestination('tool-b', 1800),))
    _, runtime, _ = _start(original)
    adoption = NOW + timedelta(minutes=20)
    changed = plan(destinations=(RoutingDestination('tool-b', 600),))
    routed, changes = resolve_alarm_routing(changed, runtime, cycle_at=adoption)
    assert routed.assignments[-1] == ToolAssignment('tool-b', adoption)
    assert len(changes) == 1
    assert changes[0].kind is AssignmentChangeKind.ASSIGNED
    assert changes[0].effective_at == adoption


def test_c2_longer_timer_reschedules_only_pending_assignment() -> None:
    original = plan(destinations=(RoutingDestination('tool-b', 900),))
    _, runtime, _ = _start(original)
    adoption = NOW + timedelta(minutes=5)
    changed = plan(destinations=(RoutingDestination('tool-b', 1800),))
    routed, changes = resolve_alarm_routing(changed, runtime, cycle_at=adoption)
    assert routed.pending_assignments == (
        PendingToolAssignment('tool-b', NOW + timedelta(minutes=30)),
    )
    assert changes[0].kind is AssignmentChangeKind.RESCHEDULED
    assert changes[0].due_at == NOW + timedelta(minutes=30)


def test_reached_assignment_never_moves_back_to_pending_when_timer_increases() -> None:
    original = plan(destinations=(RoutingDestination('tool-b', 600),))
    _, runtime, _ = _start(original)
    due = NOW + timedelta(minutes=10)
    reached, _ = resolve_alarm_routing(original, runtime, cycle_at=due)
    changed = plan(destinations=(RoutingDestination('tool-b', 1800),))
    routed, changes = resolve_alarm_routing(
        changed,
        reached,
        cycle_at=NOW + timedelta(minutes=20),
    )
    assert ToolAssignment('tool-b', due) in routed.assignments
    assert routed.pending_assignments == ()
    assert changes == ()


def test_explicit_route_removal_removes_reached_and_pending_tools() -> None:
    original = plan(
        destinations=(
            RoutingDestination('tool-b', 0),
            RoutingDestination('tool-c', 1800),
        )
    )
    _, runtime, _ = _start(original)
    changed = plan(destinations=())
    adoption = NOW + timedelta(minutes=5)
    routed, changes = resolve_alarm_routing(changed, runtime, cycle_at=adoption)
    assert routed.assignments == (ToolAssignment('tool-a', NOW),)
    assert routed.pending_assignments == ()
    assert {(change.kind, change.tool_key) for change in changes} == {
        (AssignmentChangeKind.REMOVED, 'tool-b'),
        (AssignmentChangeKind.REMOVED, 'tool-c'),
    }


def test_new_c2_destination_uses_original_occurrence_start_for_due_at() -> None:
    original = plan(destinations=())
    _, runtime, _ = _start(original)
    changed = plan(destinations=(RoutingDestination('tool-b', 1800),))
    adoption = NOW + timedelta(minutes=5)
    routed, changes = resolve_alarm_routing(changed, runtime, cycle_at=adoption)
    assert routed.pending_assignments == (
        PendingToolAssignment('tool-b', NOW + timedelta(minutes=30)),
    )
    assert changes[0].kind is AssignmentChangeKind.SCHEDULED


def test_new_overdue_destination_is_reached_at_configuration_adoption_time() -> None:
    original = plan(destinations=())
    _, runtime, _ = _start(original)
    changed = plan(destinations=(RoutingDestination('tool-b', 600),))
    adoption = NOW + timedelta(minutes=20)
    routed, changes = resolve_alarm_routing(changed, runtime, cycle_at=adoption)
    assert ToolAssignment('tool-b', adoption) in routed.assignments
    assert changes[0].kind is AssignmentChangeKind.ASSIGNED
    assert changes[0].effective_at == adoption


def test_routing_progresses_while_risk_is_eclipsed_by_active_impact() -> None:
    risk = plan('risk', destinations=(RoutingDestination('tool-b', 900),))
    impact = plan(
        'impact',
        kind=AlarmKind.IMPACT,
        criticality=Criticality.C1,
        priority_order=1,
        destinations=(RoutingDestination('tool-b'),),
    )
    ids = Ids()
    started = reduce_group_cycle(
        GroupLifecycleState(priority_group='mill-feed'),
        cycle_at=NOW,
        planned_alarms=(impact, risk),
        evaluations=(
            physical('impact', AlarmStatus.ACTIVE),
            physical('risk', AlarmStatus.ACTIVE),
        ),
        occurrence_id_factory=ids.new_occurrence,
        episode_id_factory=ids.new_episode,
    )
    due = NOW + timedelta(minutes=15)
    next_decision = reduce_group_cycle(
        started.state,
        cycle_at=due,
        planned_alarms=(impact, risk),
        evaluations=(
            physical('impact', AlarmStatus.ACTIVE, at=due),
            physical('risk', AlarmStatus.ACTIVE, at=due),
        ),
        occurrence_id_factory=ids.new_occurrence,
        episode_id_factory=ids.new_episode,
    )
    routed = next_decision.state.get(risk.identity)
    assert routed is not None
    assert ToolAssignment('tool-b', due) in routed.assignments
    assert any(
        change.kind is AssignmentChangeKind.ASSIGNED and change.alarm_identity == risk.identity
        for change in next_decision.assignment_changes
    )
