# Espejo pedagógico: resuelve ManagementEffect, reappearance y cascadas Risk/Impact sin persistencia ni Runtime.
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from ada.alarms.core.errors import AlarmContractError, AlarmLifecycleError
from ada.alarms.core.models import (
    AlarmIdentity,
    AlarmKind,
    AlarmRuntimeState,
    CascadeSuppression,
    EpisodeChange,
    EpisodeChangeKind,
    GroupLifecycleState,
    ManagementAction,
    ManagementActionOutcome,
    ManagementActionResult,
    ManagementEffect,
    ManagementEffectChange,
    ManagementEffectChangeKind,
    OccurrenceChange,
    OccurrenceChangeKind,
    PlannedAlarm,
    ReappearanceChange,
)

ManagementEffectIdFactory = Callable[[ManagementAction], str]
ReappearanceDueAtResolver = Callable[[ManagementAction], datetime]


@dataclass(frozen=True, slots=True)
class _ManagementPreparation:
    state: GroupLifecycleState
    action_results: tuple[ManagementActionResult, ...]
    effect_changes: tuple[ManagementEffectChange, ...]
    reappearance_changes: tuple[ReappearanceChange, ...]


@dataclass(frozen=True, slots=True)
class _ManagementFinalization:
    state: GroupLifecycleState
    effect_changes: tuple[ManagementEffectChange, ...]
    reappearance_changes: tuple[ReappearanceChange, ...]
    cascade_suppressions: tuple[CascadeSuppression, ...]


# Deriva si el efecto vigente pertenece exactamente a la occurrence abierta actual.
def is_directly_managed(state: AlarmRuntimeState, *, at: datetime) -> bool:
    if not isinstance(state, AlarmRuntimeState):
        raise TypeError('state must be an AlarmRuntimeState')
    _require_utc_datetime(at, 'at')
    occurrence = state.occurrence
    effect = state.management_effect
    return bool(
        occurrence is not None
        and effect is not None
        and effect.source_occurrence_id == occurrence.occurrence_id
        and effect.effective_at <= at < effect.reappearance_due_at
    )


# Deriva targets actuales; no persiste listas de targets en el hot state.
def resolve_management_cascades(
    state: GroupLifecycleState,
    *,
    planned_alarms: Sequence[PlannedAlarm],
    at: datetime,
) -> tuple[CascadeSuppression, ...]:
    if not isinstance(state, GroupLifecycleState):
        raise TypeError('state must be a GroupLifecycleState')
    _require_utc_datetime(at, 'at')
    plans = _index_plans(state.priority_group, planned_alarms)
    suppressions: list[CascadeSuppression] = []
    for source_identity, source_state in sorted(
        ((alarm.alarm_identity, alarm) for alarm in state.alarms),
        key=lambda item: item[0],
    ):
        effect = source_state.management_effect
        source_plan = plans.get(source_identity)
        if effect is None or source_plan is None:
            continue
        if not _effect_is_active(effect, at=at):
            continue
        if source_plan.kind is not AlarmKind.IMPACT or not source_plan.delivery_enabled:
            continue
        for target_identity in _cascade_target_identities(
            source_plan=source_plan,
            state=state,
            plans=plans,
        ):
            suppressions.append(
                CascadeSuppression(
                    source_alarm_identity=source_identity,
                    source_occurrence_id=effect.source_occurrence_id,
                    management_effect_id=effect.effect_id,
                    target_alarm_identity=target_identity,
                )
            )
    return tuple(
        sorted(
            suppressions,
            key=lambda item: (
                item.target_alarm_identity,
                item.source_alarm_identity,
                item.management_effect_id,
            ),
        )
    )


# Reproduce acciones y deadlines anteriores/al ciclo en orden efectivo determinístico.
def _prepare_management_state(
    state: GroupLifecycleState,
    *,
    cycle_at: datetime,
    plans: Mapping[AlarmIdentity, PlannedAlarm],
    actions: Sequence[ManagementAction],
    effect_id_factory: ManagementEffectIdFactory | None,
    due_at_resolver: ReappearanceDueAtResolver | None,
) -> _ManagementPreparation:
    _require_utc_datetime(cycle_at, 'cycle_at')
    indexed_actions = _validate_actions(actions, cycle_at=cycle_at)
    working = {alarm.alarm_identity: alarm for alarm in state.alarms}
    results: list[ManagementActionResult] = []
    effect_changes: list[ManagementEffectChange] = []
    reappearance_changes: list[ReappearanceChange] = []

    grouped: dict[datetime, list[ManagementAction]] = {}
    for action in indexed_actions:
        grouped.setdefault(action.source_created_at, []).append(action)

    for effective_at in sorted(grouped):
        action_group = sorted(
            grouped[effective_at],
            key=lambda action: (action.alarm_identity, action.input_id),
        )
        current_targets = {
            action.alarm_identity
            for action in action_group
            if _targets_current_occurrence(working.get(action.alarm_identity), action)
        }
        working, cleared, reappeared = _resolve_due_effects(
            working,
            cutoff=effective_at,
            include_equal=True,
            exclude_equal_identities=current_targets,
        )
        effect_changes.extend(cleared)
        reappearance_changes.extend(reappeared)
        for action in action_group:
            working, action_result, changes = _apply_action(
                working,
                state=state,
                plans=plans,
                action=action,
                effect_id_factory=effect_id_factory,
                due_at_resolver=due_at_resolver,
            )
            results.append(action_result)
            effect_changes.extend(changes)

    working, cleared, reappeared = _resolve_due_effects(
        working,
        cutoff=cycle_at,
        include_equal=False,
        exclude_equal_identities=set(),
    )
    effect_changes.extend(cleared)
    reappearance_changes.extend(reappeared)
    return _ManagementPreparation(
        state=GroupLifecycleState(
            priority_group=state.priority_group,
            episode=state.episode,
            alarms=tuple(working[identity] for identity in sorted(working)),
        ),
        action_results=tuple(
            sorted(
                results,
                key=lambda item: (
                    item.action.source_created_at,
                    item.action.alarm_identity,
                    item.action.input_id,
                ),
            )
        ),
        effect_changes=tuple(sorted(effect_changes, key=_effect_change_sort_key)),
        reappearance_changes=tuple(
            sorted(
                reappearance_changes,
                key=lambda item: (item.effective_at, item.alarm_identity, item.occurrence_id),
            )
        ),
    )


# Resuelve deadlines exactos y elimina efectos sin alcance después de las transiciones físicas.
def _finalize_management_state(
    state: GroupLifecycleState,
    *,
    cycle_at: datetime,
    plans: Mapping[AlarmIdentity, PlannedAlarm],
    occurrence_changes: Sequence[OccurrenceChange],
    episode_changes: Sequence[EpisodeChange],
) -> _ManagementFinalization:
    working = {alarm.alarm_identity: alarm for alarm in state.alarms}
    working, effect_changes, reappearance_changes = _resolve_due_effects(
        working,
        cutoff=cycle_at,
        include_equal=True,
        exclude_equal_identities=set(),
    )
    state_after_due = GroupLifecycleState(
        priority_group=state.priority_group,
        episode=state.episode,
        alarms=tuple(working[identity] for identity in sorted(working)),
    )
    working = {alarm.alarm_identity: alarm for alarm in state_after_due.alarms}
    cleanup_changes: list[ManagementEffectChange] = []
    for identity in sorted(tuple(working)):
        current = working[identity]
        effect = current.management_effect
        if effect is None:
            continue
        if _effect_has_scope(
            identity=identity,
            current=current,
            effect=effect,
            state=state_after_due,
            plans=plans,
        ):
            continue
        cleared_at = _scope_end_at(
            effect=effect,
            cycle_at=cycle_at,
            occurrence_changes=occurrence_changes,
            episode_changes=episode_changes,
        )
        cleanup_changes.append(
            ManagementEffectChange(
                kind=ManagementEffectChangeKind.CLEARED,
                alarm_identity=identity,
                effective_at=cleared_at,
            )
        )
        working[identity] = replace(current, management_effect=None)
        if working[identity].occurrence is None:
            del working[identity]

    final_state = GroupLifecycleState(
        priority_group=state.priority_group,
        episode=state.episode,
        alarms=tuple(working[identity] for identity in sorted(working)),
    )
    return _ManagementFinalization(
        state=final_state,
        effect_changes=tuple(
            sorted((*effect_changes, *cleanup_changes), key=_effect_change_sort_key)
        ),
        reappearance_changes=tuple(
            sorted(
                reappearance_changes,
                key=lambda item: (item.effective_at, item.alarm_identity, item.occurrence_id),
            )
        ),
        cascade_suppressions=resolve_management_cascades(
            final_state,
            planned_alarms=tuple(plans.values()),
            at=cycle_at,
        ),
    )


# Clasifica la acción contra la occurrence vigente y crea efecto sólo cuando corresponde.
def _apply_action(
    working: dict[AlarmIdentity, AlarmRuntimeState],
    *,
    state: GroupLifecycleState,
    plans: Mapping[AlarmIdentity, PlannedAlarm],
    action: ManagementAction,
    effect_id_factory: ManagementEffectIdFactory | None,
    due_at_resolver: ReappearanceDueAtResolver | None,
) -> tuple[
    dict[AlarmIdentity, AlarmRuntimeState],
    ManagementActionResult,
    tuple[ManagementEffectChange, ...],
]:
    plan = plans.get(action.alarm_identity)
    if plan is None:
        return (
            working,
            ManagementActionResult(action=action, outcome=ManagementActionOutcome.LATE),
            (),
        )
    if not plan.delivery_enabled:
        return (
            working,
            ManagementActionResult(action=action, outcome=ManagementActionOutcome.LATE),
            (),
        )
    current = working.get(action.alarm_identity)
    if _targets_current_occurrence(current, action):
        if current is None or current.occurrence is None or current.management_cycle is None:
            raise AlarmLifecycleError('current management target requires an open occurrence')
        effect = current.management_effect
        if effect is not None and effect.source_occurrence_id == current.occurrence.occurrence_id:
            if effect.reappearance_due_at == action.source_created_at:
                return _roll_management_at_due(
                    working,
                    current=current,
                    action=action,
                    effect_id_factory=effect_id_factory,
                    due_at_resolver=due_at_resolver,
                )
            return (
                working,
                ManagementActionResult(
                    action=action,
                    outcome=ManagementActionOutcome.ADDITIONAL,
                    management_cycle=current.management_cycle,
                ),
                (),
            )
        changes: list[ManagementEffectChange] = []
        if effect is not None:
            changes.append(
                ManagementEffectChange(
                    kind=ManagementEffectChangeKind.CLEARED,
                    alarm_identity=action.alarm_identity,
                    effective_at=action.source_created_at,
                )
            )
        created = _create_effect(
            action=action,
            source_occurrence_id=current.occurrence.occurrence_id,
            effect_id_factory=effect_id_factory,
            due_at_resolver=due_at_resolver,
        )
        working[action.alarm_identity] = replace(current, management_effect=created)
        changes.append(
            ManagementEffectChange(
                kind=ManagementEffectChangeKind.STARTED,
                alarm_identity=action.alarm_identity,
                effective_at=created.effective_at,
                management_effect=created,
            )
        )
        return (
            working,
            ManagementActionResult(
                action=action,
                outcome=ManagementActionOutcome.EFFECTIVE,
                management_cycle=current.management_cycle,
                management_effect_id=created.effect_id,
            ),
            tuple(changes),
        )

    result = ManagementActionResult(action=action, outcome=ManagementActionOutcome.LATE)
    if not _late_action_has_cascade_scope(
        state=state,
        working=working,
        plans=plans,
        plan=plan,
        action=action,
    ):
        return working, result, ()
    if current is not None and current.management_effect is not None:
        return working, result, ()
    if action.source_occurrence_id is None:
        return working, result, ()
    created = _create_effect(
        action=action,
        source_occurrence_id=action.source_occurrence_id,
        effect_id_factory=effect_id_factory,
        due_at_resolver=due_at_resolver,
    )
    if current is None:
        working[action.alarm_identity] = AlarmRuntimeState(
            alarm_identity=action.alarm_identity,
            management_effect=created,
        )
    else:
        working[action.alarm_identity] = replace(current, management_effect=created)
    return (
        working,
        ManagementActionResult(
            action=action,
            outcome=ManagementActionOutcome.LATE,
            management_effect_id=created.effect_id,
        ),
        (
            ManagementEffectChange(
                kind=ManagementEffectChangeKind.STARTED,
                alarm_identity=action.alarm_identity,
                effective_at=created.effective_at,
                management_effect=created,
            ),
        ),
    )


def _roll_management_at_due(
    working: dict[AlarmIdentity, AlarmRuntimeState],
    *,
    current: AlarmRuntimeState,
    action: ManagementAction,
    effect_id_factory: ManagementEffectIdFactory | None,
    due_at_resolver: ReappearanceDueAtResolver | None,
) -> tuple[
    dict[AlarmIdentity, AlarmRuntimeState],
    ManagementActionResult,
    tuple[ManagementEffectChange, ...],
]:
    if current.occurrence is None or current.management_cycle is None:
        raise AlarmLifecycleError('management rollover requires an open occurrence')
    next_cycle = current.management_cycle + 1
    created = _create_effect(
        action=action,
        source_occurrence_id=current.occurrence.occurrence_id,
        effect_id_factory=effect_id_factory,
        due_at_resolver=due_at_resolver,
    )
    working[action.alarm_identity] = replace(
        current,
        management_cycle=next_cycle,
        management_effect=created,
    )
    return (
        working,
        ManagementActionResult(
            action=action,
            outcome=ManagementActionOutcome.EFFECTIVE,
            management_cycle=next_cycle,
            management_effect_id=created.effect_id,
        ),
        (
            ManagementEffectChange(
                kind=ManagementEffectChangeKind.CLEARED,
                alarm_identity=action.alarm_identity,
                effective_at=action.source_created_at,
            ),
            ManagementEffectChange(
                kind=ManagementEffectChangeKind.STARTED,
                alarm_identity=action.alarm_identity,
                effective_at=created.effective_at,
                management_effect=created,
            ),
        ),
    )


def _create_effect(
    *,
    action: ManagementAction,
    source_occurrence_id: str,
    effect_id_factory: ManagementEffectIdFactory | None,
    due_at_resolver: ReappearanceDueAtResolver | None,
) -> ManagementEffect:
    if effect_id_factory is None:
        raise AlarmContractError(
            'management_effect_id_factory is required for effective management'
        )
    if due_at_resolver is None:
        raise AlarmContractError(
            'reappearance_due_at_resolver is required for effective management'
        )
    effect_id = effect_id_factory(action)
    if not isinstance(effect_id, str) or not effect_id.strip():
        raise AlarmLifecycleError('management effect factory must return a non-empty string')
    due_at = due_at_resolver(action)
    _require_utc_datetime(due_at, 'reappearance_due_at')
    return ManagementEffect(
        effect_id=effect_id,
        source_occurrence_id=source_occurrence_id,
        effective_at=action.source_created_at,
        reappearance_due_at=due_at,
    )


# Expira efectos por deadline absoluto y genera reappearance sólo si la misma occurrence sigue abierta.
def _resolve_due_effects(
    working: dict[AlarmIdentity, AlarmRuntimeState],
    *,
    cutoff: datetime,
    include_equal: bool,
    exclude_equal_identities: set[AlarmIdentity],
) -> tuple[
    dict[AlarmIdentity, AlarmRuntimeState],
    tuple[ManagementEffectChange, ...],
    tuple[ReappearanceChange, ...],
]:
    changes: list[ManagementEffectChange] = []
    reappearances: list[ReappearanceChange] = []
    for identity in sorted(tuple(working)):
        current = working[identity]
        effect = current.management_effect
        if effect is None:
            continue
        due = effect.reappearance_due_at
        if due > cutoff:
            continue
        if due == cutoff and (not include_equal or identity in exclude_equal_identities):
            continue
        changes.append(
            ManagementEffectChange(
                kind=ManagementEffectChangeKind.CLEARED,
                alarm_identity=identity,
                effective_at=due,
            )
        )
        next_state = replace(current, management_effect=None)
        if (
            current.occurrence is not None
            and current.management_cycle is not None
            and effect.source_occurrence_id == current.occurrence.occurrence_id
        ):
            cycle = current.management_cycle + 1
            next_state = replace(next_state, management_cycle=cycle)
            reappearances.append(
                ReappearanceChange(
                    alarm_identity=identity,
                    occurrence_id=current.occurrence.occurrence_id,
                    effective_at=due,
                    management_cycle=cycle,
                )
            )
        working[identity] = next_state
        if next_state.occurrence is None and next_state.management_effect is None:
            del working[identity]
    return working, tuple(changes), tuple(reappearances)


# Determina si un LATE de Impact aún puede afectar Risk activas del mismo Episode.
def _late_action_has_cascade_scope(
    *,
    state: GroupLifecycleState,
    working: Mapping[AlarmIdentity, AlarmRuntimeState],
    plans: Mapping[AlarmIdentity, PlannedAlarm],
    plan: PlannedAlarm,
    action: ManagementAction,
) -> bool:
    if state.episode is None:
        return False
    if plan.kind is not AlarmKind.IMPACT or not plan.delivery_enabled:
        return False
    if action.source_occurrence_id is None:
        return False
    if action.source_created_at < state.episode.started_at:
        return False
    current = working.get(action.alarm_identity)
    if current is not None and current.occurrence is not None:
        if current.occurrence.occurrence_id == action.source_occurrence_id:
            return False
        if action.source_created_at >= current.occurrence.started_at:
            return False
    return bool(
        _cascade_target_identities(
            source_plan=plan,
            state=GroupLifecycleState(
                priority_group=state.priority_group,
                episode=state.episode,
                alarms=tuple(working[identity] for identity in sorted(working)),
            ),
            plans=plans,
        )
    )


# Mantiene un efecto tras cerrar su source occurrence sólo mientras conserve alcance operacional.
def _effect_has_scope(
    *,
    identity: AlarmIdentity,
    current: AlarmRuntimeState,
    effect: ManagementEffect,
    state: GroupLifecycleState,
    plans: Mapping[AlarmIdentity, PlannedAlarm],
) -> bool:
    if state.episode is None:
        return False
    if (
        current.occurrence is not None
        and current.occurrence.occurrence_id == effect.source_occurrence_id
    ):
        return True
    source_plan = plans.get(identity)
    if source_plan is None:
        return False
    if source_plan.kind is not AlarmKind.IMPACT or not source_plan.delivery_enabled:
        return False
    return bool(
        _cascade_target_identities(
            source_plan=source_plan,
            state=state,
            plans=plans,
        )
    )


# Selecciona Risk visibles de menor prioridad dentro del mismo priority_group.
def _cascade_target_identities(
    *,
    source_plan: PlannedAlarm,
    state: GroupLifecycleState,
    plans: Mapping[AlarmIdentity, PlannedAlarm],
) -> tuple[AlarmIdentity, ...]:
    targets: list[AlarmIdentity] = []
    for target_state in state.alarms:
        if target_state.occurrence is None:
            continue
        target_plan = plans.get(target_state.alarm_identity)
        if target_plan is None:
            continue
        if target_plan.kind is not AlarmKind.RISK or not target_plan.delivery_enabled:
            continue
        if target_plan.priority_order <= source_plan.priority_order:
            continue
        targets.append(target_plan.identity)
    return tuple(sorted(targets))


def _scope_end_at(
    *,
    effect: ManagementEffect,
    cycle_at: datetime,
    occurrence_changes: Sequence[OccurrenceChange],
    episode_changes: Sequence[EpisodeChange],
) -> datetime:
    for change in occurrence_changes:
        if (
            change.kind is OccurrenceChangeKind.CLOSED
            and change.occurrence.occurrence_id == effect.source_occurrence_id
            and change.occurrence.ended_at is not None
        ):
            return change.occurrence.ended_at
    for change in episode_changes:
        if change.kind is EpisodeChangeKind.CLOSED and change.episode.ended_at is not None:
            return change.episode.ended_at
    return cycle_at


def _targets_current_occurrence(
    current: AlarmRuntimeState | None,
    action: ManagementAction,
) -> bool:
    if current is None or current.occurrence is None:
        return False
    return action.source_occurrence_id in {None, current.occurrence.occurrence_id}


def _validate_actions(
    actions: Sequence[ManagementAction],
    *,
    cycle_at: datetime,
) -> tuple[ManagementAction, ...]:
    seen: set[str] = set()
    result: list[ManagementAction] = []
    for action in actions:
        if not isinstance(action, ManagementAction):
            raise TypeError('management_actions must contain ManagementAction values')
        if action.input_id in seen:
            raise AlarmContractError(
                'management_actions must not contain duplicate input_id values'
            )
        if action.source_created_at > cycle_at:
            raise AlarmContractError(
                'management action source_created_at must not be after cycle_at'
            )
        seen.add(action.input_id)
        result.append(action)
    return tuple(result)


def _index_plans(
    priority_group: str,
    planned_alarms: Sequence[PlannedAlarm],
) -> dict[AlarmIdentity, PlannedAlarm]:
    result: dict[AlarmIdentity, PlannedAlarm] = {}
    priority_orders: set[int] = set()
    impact_orders: list[int] = []
    risk_orders: list[int] = []
    for plan in planned_alarms:
        if not isinstance(plan, PlannedAlarm):
            raise TypeError('planned_alarms must contain PlannedAlarm values')
        if plan.priority_group != priority_group:
            raise AlarmContractError('planned alarm priority_group does not match group state')
        if plan.identity in result:
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
        result[plan.identity] = plan
    if impact_orders and risk_orders and max(impact_orders) >= min(risk_orders):
        raise AlarmContractError(
            'IMPACT priority_order values must be lower than RISK values within priority_group'
        )
    return result


def _effect_is_active(effect: ManagementEffect, *, at: datetime) -> bool:
    return effect.effective_at <= at < effect.reappearance_due_at


def _effect_change_sort_key(
    change: ManagementEffectChange,
) -> tuple[datetime, AlarmIdentity, str]:
    return change.effective_at, change.alarm_identity, change.kind.value


def _require_utc_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f'{name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
        raise ValueError(f'{name} must be timezone-aware UTC')
