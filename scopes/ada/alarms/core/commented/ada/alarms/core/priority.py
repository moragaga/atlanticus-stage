# Espejo pedagógico de Priority Resolution.
# La prioridad continúa siendo derivada y no se persiste como winner/suppressed/eligible.
# Una alarma con DeactivationEffect vigente queda fuera de candidatos operacionales y se clasifica DEACTIVATED.
# Shadow se mantiene separado: una alarma no entregable sigue siendo SHADOW y no adquiere efectos accionables.

from __future__ import annotations

from collections.abc import Sequence

from ada.alarms.core.errors import AlarmContractError
from ada.alarms.core.models import (
    AlarmIdentity,
    AlarmKind,
    AlarmPriorityDecision,
    CascadeSuppression,
    GroupLifecycleState,
    GroupPriorityResolution,
    PlannedAlarm,
    PriorityDisposition,
)


def resolve_group_priority(
    state: GroupLifecycleState,
    *,
    planned_alarms: Sequence[PlannedAlarm],
    cascade_suppressions: Sequence[CascadeSuppression] = (),
) -> GroupPriorityResolution:
    if not isinstance(state, GroupLifecycleState):
        raise TypeError('state must be a GroupLifecycleState')
    plans = _index_plans(state.priority_group, planned_alarms)
    cascades = _index_cascades(cascade_suppressions)
    active = {alarm.alarm_identity: alarm for alarm in state.alarms if alarm.occurrence is not None}
    candidates = [
        plans[identity]
        for identity, current in active.items()
        if identity in plans
        and plans[identity].delivery_enabled
        and identity not in cascades
        and current.deactivation_effect is None
    ]
    predominant = min(candidates, key=lambda plan: plan.priority_order) if candidates else None
    decisions: list[AlarmPriorityDecision] = []
    for identity in sorted(active):
        plan = plans.get(identity)
        if plan is None:
            raise AlarmContractError(
                'open occurrence requires a planned alarm for priority resolution'
            )
        if not plan.delivery_enabled:
            decisions.append(
                AlarmPriorityDecision(
                    alarm_identity=identity,
                    disposition=PriorityDisposition.SHADOW,
                )
            )
            continue
        if active[identity].deactivation_effect is not None:
            decisions.append(
                AlarmPriorityDecision(
                    alarm_identity=identity,
                    disposition=PriorityDisposition.DEACTIVATED,
                )
            )
            continue
        blockers = cascades.get(identity, ())
        if blockers:
            decisions.append(
                AlarmPriorityDecision(
                    alarm_identity=identity,
                    disposition=PriorityDisposition.CASCADE_SUPPRESSED,
                    blocking_alarm_identities=blockers,
                )
            )
            continue
        if predominant is not None and identity == predominant.identity:
            decisions.append(
                AlarmPriorityDecision(
                    alarm_identity=identity,
                    disposition=PriorityDisposition.PREDOMINANT,
                )
            )
            continue
        if predominant is None:
            raise AlarmContractError(
                'priority resolution has active operational alarm without candidate'
            )
        decisions.append(
            AlarmPriorityDecision(
                alarm_identity=identity,
                disposition=PriorityDisposition.ECLIPSED,
                blocking_alarm_identities=(predominant.identity,),
            )
        )
    return GroupPriorityResolution(
        priority_group=state.priority_group,
        predominant_alarm_identity=(None if predominant is None else predominant.identity),
        alarms=tuple(decisions),
    )


def _index_cascades(
    suppressions: Sequence[CascadeSuppression],
) -> dict[AlarmIdentity, tuple[AlarmIdentity, ...]]:
    grouped: dict[AlarmIdentity, set[AlarmIdentity]] = {}
    for suppression in suppressions:
        if not isinstance(suppression, CascadeSuppression):
            raise TypeError('cascade_suppressions must contain CascadeSuppression values')
        grouped.setdefault(suppression.target_alarm_identity, set()).add(
            suppression.source_alarm_identity
        )
    return {identity: tuple(sorted(sources)) for identity, sources in grouped.items()}


def _index_plans(
    priority_group: str,
    planned_alarms: Sequence[PlannedAlarm],
) -> dict[AlarmIdentity, PlannedAlarm]:
    plans: dict[AlarmIdentity, PlannedAlarm] = {}
    priority_orders: set[int] = set()
    impact_orders: list[int] = []
    risk_orders: list[int] = []
    for plan in planned_alarms:
        if not isinstance(plan, PlannedAlarm):
            raise TypeError('planned_alarms must contain PlannedAlarm values')
        if plan.priority_group != priority_group:
            raise AlarmContractError('planned alarm priority_group does not match group state')
        if plan.identity in plans:
            raise AlarmContractError('planned_alarms must not contain duplicate identities')
        if plan.priority_order in priority_orders:
            raise AlarmContractError(
                'planned_alarms must not contain duplicate priority_order values'
            )
        priority_orders.add(plan.priority_order)
        if plan.kind is AlarmKind.IMPACT:
            impact_orders.append(plan.priority_order)
        else:
            risk_orders.append(plan.priority_order)
        plans[plan.identity] = plan
    if impact_orders and risk_orders and max(impact_orders) >= min(risk_orders):
        raise AlarmContractError(
            'IMPACT priority_order values must be lower than RISK values within priority_group'
        )
    return plans
