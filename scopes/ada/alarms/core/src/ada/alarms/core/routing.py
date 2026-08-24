from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ada.alarms.core.errors import AlarmContractError
from ada.alarms.core.models import (
    AlarmIdentity,
    AlarmRuntimeState,
    AssignmentChange,
    AssignmentChangeKind,
    Criticality,
    GroupLifecycleState,
    PendingToolAssignment,
    PlannedAlarm,
    ToolAssignment,
)


@dataclass(frozen=True, slots=True)
class GroupRoutingDecision:
    state: GroupLifecycleState
    assignment_changes: tuple[AssignmentChange, ...]


def resolve_group_routing(
    state: GroupLifecycleState,
    *,
    planned_alarms: Sequence[PlannedAlarm],
    cycle_at: datetime,
) -> GroupRoutingDecision:
    if not isinstance(state, GroupLifecycleState):
        raise TypeError('state must be a GroupLifecycleState')
    _require_utc_datetime(cycle_at, 'cycle_at')
    plans = _index_plans(state.priority_group, planned_alarms)
    updated: dict[AlarmIdentity, AlarmRuntimeState] = {}
    changes: list[AssignmentChange] = []
    for current in sorted(state.alarms, key=lambda item: item.alarm_identity):
        if current.occurrence is None:
            updated[current.alarm_identity] = current
            continue
        plan = plans.get(current.alarm_identity)
        if plan is None:
            raise AlarmContractError('open occurrence requires a planned alarm for routing')
        routed, alarm_changes = resolve_alarm_routing(plan, current, cycle_at=cycle_at)
        updated[current.alarm_identity] = routed
        changes.extend(alarm_changes)
    return GroupRoutingDecision(
        state=GroupLifecycleState(
            priority_group=state.priority_group,
            episode=state.episode,
            alarms=tuple(updated[identity] for identity in sorted(updated)),
        ),
        assignment_changes=tuple(
            sorted(
                changes,
                key=lambda change: (
                    change.effective_at,
                    change.alarm_identity,
                    change.tool_key,
                    change.kind.value,
                ),
            )
        ),
    )


def resolve_alarm_routing(
    plan: PlannedAlarm,
    state: AlarmRuntimeState,
    *,
    cycle_at: datetime,
) -> tuple[AlarmRuntimeState, tuple[AssignmentChange, ...]]:
    if not isinstance(plan, PlannedAlarm):
        raise TypeError('plan must be a PlannedAlarm')
    if not isinstance(state, AlarmRuntimeState):
        raise TypeError('state must be an AlarmRuntimeState')
    _require_utc_datetime(cycle_at, 'cycle_at')
    if state.alarm_identity != plan.identity:
        raise AlarmContractError('routing plan identity must match runtime state')
    occurrence = state.occurrence
    if occurrence is None:
        return state, ()
    targets = _target_due_times(plan, occurrence.started_at)
    assigned = {item.tool_key: item for item in state.assignments}
    pending = {item.tool_key: item for item in state.pending_assignments}
    next_assigned: dict[str, ToolAssignment] = {}
    next_pending: dict[str, PendingToolAssignment] = {}
    changes: list[AssignmentChange] = []

    for tool_key in sorted((set(assigned) | set(pending)) - set(targets)):
        if tool_key not in targets:
            changes.append(
                AssignmentChange(
                    kind=AssignmentChangeKind.REMOVED,
                    alarm_identity=plan.identity,
                    tool_key=tool_key,
                    effective_at=cycle_at,
                )
            )

    for tool_key, due_at in sorted(targets.items()):
        existing_assignment = assigned.get(tool_key)
        if existing_assignment is not None:
            next_assigned[tool_key] = existing_assignment
            continue
        existing_pending = pending.get(tool_key)
        if existing_pending is not None:
            if due_at <= cycle_at:
                assigned_at = (
                    existing_pending.due_at if existing_pending.due_at == due_at else cycle_at
                )
                assignment = ToolAssignment(tool_key=tool_key, assigned_at=assigned_at)
                next_assigned[tool_key] = assignment
                changes.append(
                    AssignmentChange(
                        kind=AssignmentChangeKind.ASSIGNED,
                        alarm_identity=plan.identity,
                        tool_key=tool_key,
                        effective_at=assigned_at,
                    )
                )
            else:
                next_pending[tool_key] = PendingToolAssignment(tool_key=tool_key, due_at=due_at)
                if due_at != existing_pending.due_at:
                    changes.append(
                        AssignmentChange(
                            kind=AssignmentChangeKind.RESCHEDULED,
                            alarm_identity=plan.identity,
                            tool_key=tool_key,
                            effective_at=cycle_at,
                            due_at=due_at,
                        )
                    )
            continue
        if due_at <= cycle_at:
            assigned_at = due_at if occurrence.started_at == cycle_at else cycle_at
            assignment = ToolAssignment(tool_key=tool_key, assigned_at=assigned_at)
            next_assigned[tool_key] = assignment
            changes.append(
                AssignmentChange(
                    kind=AssignmentChangeKind.ASSIGNED,
                    alarm_identity=plan.identity,
                    tool_key=tool_key,
                    effective_at=assigned_at,
                )
            )
        else:
            next_pending[tool_key] = PendingToolAssignment(tool_key=tool_key, due_at=due_at)
            changes.append(
                AssignmentChange(
                    kind=AssignmentChangeKind.SCHEDULED,
                    alarm_identity=plan.identity,
                    tool_key=tool_key,
                    effective_at=cycle_at,
                    due_at=due_at,
                )
            )

    return (
        replace(
            state,
            assignments=tuple(next_assigned.values()),
            pending_assignments=tuple(next_pending.values()),
        ),
        tuple(
            sorted(
                changes,
                key=lambda change: (
                    change.effective_at,
                    change.tool_key,
                    change.kind.value,
                ),
            )
        ),
    )


def _target_due_times(plan: PlannedAlarm, started_at: datetime) -> dict[str, datetime]:
    targets = {plan.routing.origin_tool_key: started_at}
    if plan.criticality is Criticality.C3:
        return targets
    for destination in plan.routing.destinations:
        delay_seconds = 0 if plan.criticality is Criticality.C1 else destination.delay_seconds
        if delay_seconds is None:
            raise AlarmContractError('C2 routing destination is missing delay_seconds')
        targets[destination.tool_key] = started_at + timedelta(seconds=delay_seconds)
    return targets


def _index_plans(
    priority_group: str,
    planned_alarms: Sequence[PlannedAlarm],
) -> Mapping[AlarmIdentity, PlannedAlarm]:
    plans: dict[AlarmIdentity, PlannedAlarm] = {}
    for plan in planned_alarms:
        if not isinstance(plan, PlannedAlarm):
            raise TypeError('planned_alarms must contain PlannedAlarm values')
        if plan.priority_group != priority_group:
            raise AlarmContractError('planned alarm priority_group does not match group state')
        if plan.identity in plans:
            raise AlarmContractError('planned_alarms must not contain duplicate identities')
        plans[plan.identity] = plan
    return plans


def _require_utc_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f'{name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f'{name} must be timezone-aware UTC')
