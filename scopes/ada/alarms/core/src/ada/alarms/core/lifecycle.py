from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from ada.alarms.core.deactivation import (
    DeactivationEffectIdFactory,
    DeactivationRequestIdFactory,
)
from ada.alarms.core.errors import AlarmContractError, AlarmLifecycleError
from ada.alarms.core.management import (
    ManagementEffectIdFactory,
    ReappearanceDueAtResolver,
    _finalize_management_state,
    _prepare_management_state,
)
from ada.alarms.core.models import (
    TECHNICAL_HOLD_GRACE_SECONDS,
    AlarmEpisode,
    AlarmEvaluation,
    AlarmIdentity,
    AlarmKind,
    AlarmOccurrence,
    AlarmRuntimeState,
    AlarmStatus,
    ConfigurationClosure,
    DeactivationDecision,
    DeactivationEffectChange,
    DeactivationEffectChangeKind,
    DeactivationRequest,
    EpisodeChange,
    EpisodeChangeKind,
    EpisodeClosureReason,
    GroupLifecycleDecision,
    GroupLifecycleState,
    ManagementAction,
    ManagementEffectChange,
    ManagementEffectChangeKind,
    OccurrenceChange,
    OccurrenceChangeKind,
    OccurrenceClosureReason,
    PlannedAlarm,
    TechnicalHold,
    TechnicalHoldChange,
    TechnicalHoldChangeKind,
)
from ada.alarms.core.priority import resolve_group_priority
from ada.alarms.core.routing import resolve_group_routing

OccurrenceIdFactory = Callable[[AlarmIdentity, datetime], str]
EpisodeIdFactory = Callable[[str, datetime], str]


@dataclass(frozen=True, slots=True)
class _AlarmResolution:
    retained_state: AlarmRuntimeState | None
    start_evaluation: AlarmEvaluation | None = None
    closed_occurrence: AlarmOccurrence | None = None
    technical_hold_changes: tuple[TechnicalHoldChange, ...] = ()


def reduce_group_cycle(
    state: GroupLifecycleState,
    *,
    cycle_at: datetime,
    planned_alarms: Sequence[PlannedAlarm],
    evaluations: Sequence[AlarmEvaluation],
    occurrence_id_factory: OccurrenceIdFactory,
    episode_id_factory: EpisodeIdFactory,
    configuration_closures: Sequence[ConfigurationClosure] = (),
    management_actions: Sequence[ManagementAction] = (),
    management_effect_id_factory: ManagementEffectIdFactory | None = None,
    reappearance_due_at_resolver: ReappearanceDueAtResolver | None = None,
    pending_deactivation_requests: Sequence[DeactivationRequest] = (),
    deactivation_decisions: Sequence[DeactivationDecision] = (),
    deactivation_request_id_factory: DeactivationRequestIdFactory | None = None,
    deactivation_effect_id_factory: DeactivationEffectIdFactory | None = None,
    technical_hold_grace_seconds: int = TECHNICAL_HOLD_GRACE_SECONDS,
) -> GroupLifecycleDecision:
    _validate_cycle_at(cycle_at)
    _validate_grace(technical_hold_grace_seconds)
    plans = _index_plans(state.priority_group, planned_alarms)
    evaluation_map = _index_evaluations(cycle_at, evaluations)
    closures = _index_closures(cycle_at, configuration_closures)
    _validate_evaluation_cardinality(state, plans, evaluation_map, closures)
    management = _prepare_management_state(
        state,
        cycle_at=cycle_at,
        plans=plans,
        actions=management_actions,
        effect_id_factory=management_effect_id_factory,
        due_at_resolver=reappearance_due_at_resolver,
        pending_deactivation_requests=pending_deactivation_requests,
        deactivation_decisions=deactivation_decisions,
        deactivation_request_id_factory=deactivation_request_id_factory,
        deactivation_effect_id_factory=deactivation_effect_id_factory,
    )

    working = {alarm.alarm_identity: alarm for alarm in management.state.alarms}
    episode = management.state.episode
    occurrence_changes: list[OccurrenceChange] = []
    episode_changes: list[EpisodeChange] = []
    technical_hold_changes: list[TechnicalHoldChange] = []
    lifecycle_management_effect_changes: list[ManagementEffectChange] = []
    used_occurrence_ids = {
        alarm.occurrence.occurrence_id
        for alarm in management.state.alarms
        if alarm.occurrence is not None
    }
    used_episode_ids = (
        {management.state.episode.episode_id} if management.state.episode is not None else set()
    )

    overdue_due_times = sorted(
        {
            alarm.technical_hold.due_at
            for alarm in working.values()
            if alarm.occurrence is not None
            and alarm.technical_hold is not None
            and alarm.technical_hold.due_at < cycle_at
        }
    )
    for due_at in overdue_due_times:
        expiring = [
            identity
            for identity, alarm in working.items()
            if alarm.occurrence is not None
            and alarm.technical_hold is not None
            and alarm.technical_hold.due_at == due_at
        ]
        for identity in sorted(expiring):
            current = working[identity]
            occurrence = current.occurrence
            if occurrence is None:
                raise AlarmLifecycleError('technical hold expiry requires an open occurrence')
            closed = occurrence.close(
                ended_at=due_at,
                reason=OccurrenceClosureReason.TECHNICAL_HOLD_EXPIRED,
            )
            occurrence_changes.append(
                OccurrenceChange(kind=OccurrenceChangeKind.CLOSED, occurrence=closed)
            )
            technical_hold_changes.append(
                TechnicalHoldChange(
                    kind=TechnicalHoldChangeKind.CLEARED,
                    alarm_identity=identity,
                    occurrence_id=occurrence.occurrence_id,
                    effective_at=due_at,
                )
            )
            management_only = _management_only_state(current)
            if management_only is None:
                del working[identity]
            else:
                working[identity] = management_only
        if episode is not None and not _has_open_occurrence(working):
            closed_episode = episode.close(
                ended_at=due_at,
                reason=EpisodeClosureReason.TECHNICAL_UNCERTAINTY,
            )
            episode_changes.append(
                EpisodeChange(kind=EpisodeChangeKind.CLOSED, episode=closed_episode)
            )
            _clear_management_only_states(
                working,
                effective_at=due_at,
                changes=lifecycle_management_effect_changes,
            )
            episode = None

    retained: dict[AlarmIdentity, AlarmRuntimeState] = {}
    pending_starts: list[tuple[PlannedAlarm, AlarmEvaluation]] = []
    cycle_closure_reasons: list[OccurrenceClosureReason] = []
    identities = sorted(set(plans) | set(closures) | set(working))
    for identity in identities:
        current = working.get(identity)
        plan = plans.get(identity)
        evaluation = evaluation_map.get(identity)
        closure = closures.get(identity)
        resolution = _resolve_cycle_alarm(
            current=current,
            evaluation=evaluation,
            closure=closure,
            cycle_at=cycle_at,
            technical_hold_grace_seconds=technical_hold_grace_seconds,
        )
        technical_hold_changes.extend(resolution.technical_hold_changes)
        if resolution.closed_occurrence is not None:
            occurrence_changes.append(
                OccurrenceChange(
                    kind=OccurrenceChangeKind.CLOSED,
                    occurrence=resolution.closed_occurrence,
                )
            )
            cycle_closure_reasons.append(resolution.closed_occurrence.closure_reason)
        if resolution.retained_state is not None:
            retained[identity] = resolution.retained_state
        if resolution.start_evaluation is not None:
            if plan is None:
                raise AlarmLifecycleError('new occurrence requires a planned alarm')
            pending_starts.append((plan, resolution.start_evaluation))

    if episode is None and pending_starts:
        episode_id = _require_generated_id(
            episode_id_factory(state.priority_group, cycle_at),
            'episode_id',
        )
        if episode_id in used_episode_ids:
            raise AlarmLifecycleError('episode_id factory returned a duplicate identifier')
        used_episode_ids.add(episode_id)
        episode = AlarmEpisode(
            episode_id=episode_id,
            priority_group=state.priority_group,
            started_at=cycle_at,
        )
        episode_changes.append(EpisodeChange(kind=EpisodeChangeKind.STARTED, episode=episode))

    if pending_starts:
        if episode is None:
            raise AlarmLifecycleError('new occurrence requires an open episode')
        for plan, evaluation in sorted(pending_starts, key=lambda item: item[0].identity):
            occurrence_id = _require_generated_id(
                occurrence_id_factory(plan.identity, cycle_at),
                'occurrence_id',
            )
            if occurrence_id in used_occurrence_ids:
                raise AlarmLifecycleError('occurrence_id factory returned a duplicate identifier')
            used_occurrence_ids.add(occurrence_id)
            occurrence = AlarmOccurrence(
                occurrence_id=occurrence_id,
                alarm_identity=plan.identity,
                episode_id=episode.episode_id,
                started_at=evaluation.evaluated_at,
                alarm_configuration_revision=plan.alarm_configuration_revision,
                tool_registry_revision=plan.tool_registry_revision,
            )
            previous = retained.get(plan.identity)
            retained[plan.identity] = AlarmRuntimeState(
                alarm_identity=plan.identity,
                occurrence=occurrence,
                last_evaluation=evaluation,
                management_cycle=1,
                management_effect=(None if previous is None else previous.management_effect),
                deactivation_effect=(None if previous is None else previous.deactivation_effect),
            )
            occurrence_changes.append(
                OccurrenceChange(kind=OccurrenceChangeKind.STARTED, occurrence=occurrence)
            )

    if episode is not None and not _has_open_occurrence(retained):
        reason = _episode_closure_reason(cycle_closure_reasons)
        closed_episode = episode.close(ended_at=cycle_at, reason=reason)
        episode_changes.append(EpisodeChange(kind=EpisodeChangeKind.CLOSED, episode=closed_episode))
        _clear_management_only_states(
            retained,
            effective_at=cycle_at,
            changes=lifecycle_management_effect_changes,
        )
        episode = None

    next_state = GroupLifecycleState(
        priority_group=state.priority_group,
        episode=episode,
        alarms=tuple(retained[identity] for identity in sorted(retained)),
    )
    sorted_occurrence_changes = tuple(sorted(occurrence_changes, key=_occurrence_change_sort_key))
    sorted_episode_changes = tuple(sorted(episode_changes, key=_episode_change_sort_key))
    finalized_management = _finalize_management_state(
        next_state,
        cycle_at=cycle_at,
        plans=plans,
        occurrence_changes=sorted_occurrence_changes,
        episode_changes=sorted_episode_changes,
    )
    routing = resolve_group_routing(
        finalized_management.state,
        planned_alarms=tuple(plans.values()),
        cycle_at=cycle_at,
    )
    priority = resolve_group_priority(
        routing.state,
        planned_alarms=tuple(plans.values()),
        cascade_suppressions=finalized_management.cascade_suppressions,
    )
    return GroupLifecycleDecision(
        state=routing.state,
        occurrence_changes=sorted_occurrence_changes,
        episode_changes=sorted_episode_changes,
        technical_hold_changes=tuple(
            sorted(
                technical_hold_changes,
                key=lambda change: (change.effective_at, change.alarm_identity, change.kind.value),
            )
        ),
        management_action_results=management.action_results,
        deactivation_request_results=management.deactivation_request_results,
        deactivation_decision_results=management.deactivation_decision_results,
        deactivation_effect_changes=management.deactivation_effect_changes,
        management_effect_changes=tuple(
            sorted(
                (
                    *management.effect_changes,
                    *lifecycle_management_effect_changes,
                    *finalized_management.effect_changes,
                ),
                key=lambda change: (
                    change.effective_at,
                    change.alarm_identity,
                    change.kind.value,
                ),
            )
        ),
        reappearance_changes=tuple(
            sorted(
                (*management.reappearance_changes, *finalized_management.reappearance_changes),
                key=lambda change: (
                    change.effective_at,
                    change.alarm_identity,
                    change.occurrence_id,
                ),
            )
        ),
        cascade_suppressions=finalized_management.cascade_suppressions,
        assignment_changes=routing.assignment_changes,
        priority_resolution=priority,
    )


def reset_group_for_reconfiguration(
    state: GroupLifecycleState,
    *,
    effective_at: datetime,
) -> GroupLifecycleDecision:
    _validate_cycle_at(effective_at)
    occurrence_changes: list[OccurrenceChange] = []
    technical_hold_changes: list[TechnicalHoldChange] = []
    management_effect_changes: list[ManagementEffectChange] = []
    deactivation_effect_changes: list[DeactivationEffectChange] = []
    for alarm in sorted(state.alarms, key=lambda item: item.alarm_identity):
        if alarm.occurrence is not None:
            closed = alarm.occurrence.close(
                ended_at=effective_at,
                reason=OccurrenceClosureReason.CONFIGURATION_RECONFIGURED,
            )
            occurrence_changes.append(
                OccurrenceChange(kind=OccurrenceChangeKind.CLOSED, occurrence=closed)
            )
            if alarm.technical_hold is not None:
                technical_hold_changes.append(
                    TechnicalHoldChange(
                        kind=TechnicalHoldChangeKind.CLEARED,
                        alarm_identity=alarm.alarm_identity,
                        occurrence_id=alarm.occurrence.occurrence_id,
                        effective_at=effective_at,
                    )
                )
        if alarm.management_effect is not None:
            management_effect_changes.append(
                ManagementEffectChange(
                    kind=ManagementEffectChangeKind.CLEARED,
                    alarm_identity=alarm.alarm_identity,
                    effective_at=effective_at,
                )
            )
        if alarm.deactivation_effect is not None:
            deactivation_effect_changes.append(
                DeactivationEffectChange(
                    kind=DeactivationEffectChangeKind.CLEARED,
                    alarm_identity=alarm.alarm_identity,
                    effective_at=effective_at,
                )
            )
    episode_changes: tuple[EpisodeChange, ...] = ()
    if state.episode is not None:
        closed_episode = state.episode.close(
            ended_at=effective_at,
            reason=EpisodeClosureReason.CONFIGURATION_TERMINATED,
        )
        episode_changes = (EpisodeChange(kind=EpisodeChangeKind.CLOSED, episode=closed_episode),)
    return GroupLifecycleDecision(
        state=GroupLifecycleState(priority_group=state.priority_group),
        occurrence_changes=tuple(occurrence_changes),
        episode_changes=episode_changes,
        technical_hold_changes=tuple(technical_hold_changes),
        management_effect_changes=tuple(management_effect_changes),
        deactivation_effect_changes=tuple(deactivation_effect_changes),
    )


def _resolve_cycle_alarm(
    *,
    current: AlarmRuntimeState | None,
    evaluation: AlarmEvaluation | None,
    closure: ConfigurationClosure | None,
    cycle_at: datetime,
    technical_hold_grace_seconds: int,
) -> _AlarmResolution:
    if current is None or current.occurrence is None:
        if closure is not None:
            return _AlarmResolution(retained_state=current)
        if evaluation is None or evaluation.status is not AlarmStatus.ACTIVE:
            return _AlarmResolution(retained_state=current)
        return _AlarmResolution(retained_state=current, start_evaluation=evaluation)

    occurrence = current.occurrence
    if closure is not None and not (
        evaluation is not None and evaluation.status is AlarmStatus.INACTIVE
    ):
        closed = occurrence.close(ended_at=cycle_at, reason=closure.reason)
        return _AlarmResolution(
            retained_state=_management_only_state(current),
            closed_occurrence=closed,
            technical_hold_changes=_clear_hold_if_needed(current, cycle_at),
        )
    if evaluation is None:
        return _AlarmResolution(retained_state=current)
    if evaluation.status is AlarmStatus.INACTIVE:
        closed = occurrence.close(
            ended_at=evaluation.evaluated_at,
            reason=OccurrenceClosureReason.CONDITION_NORMALIZED,
        )
        return _AlarmResolution(
            retained_state=_management_only_state(current),
            closed_occurrence=closed,
            technical_hold_changes=_clear_hold_if_needed(current, cycle_at),
        )
    if evaluation.status is AlarmStatus.ACTIVE:
        if current.technical_hold is None:
            return _AlarmResolution(retained_state=current)
        cleared = TechnicalHoldChange(
            kind=TechnicalHoldChangeKind.CLEARED,
            alarm_identity=current.alarm_identity,
            occurrence_id=occurrence.occurrence_id,
            effective_at=cycle_at,
        )
        return _AlarmResolution(
            retained_state=AlarmRuntimeState(
                alarm_identity=current.alarm_identity,
                occurrence=occurrence,
                last_evaluation=evaluation,
                management_cycle=current.management_cycle,
                management_effect=current.management_effect,
                deactivation_effect=current.deactivation_effect,
                assignments=current.assignments,
                pending_assignments=current.pending_assignments,
            ),
            technical_hold_changes=(cleared,),
        )
    if current.technical_hold is None:
        hold = TechnicalHold(
            started_at=evaluation.evaluated_at,
            due_at=evaluation.evaluated_at + timedelta(seconds=technical_hold_grace_seconds),
        )
        started = TechnicalHoldChange(
            kind=TechnicalHoldChangeKind.STARTED,
            alarm_identity=current.alarm_identity,
            occurrence_id=occurrence.occurrence_id,
            effective_at=evaluation.evaluated_at,
            technical_hold=hold,
        )
        return _AlarmResolution(
            retained_state=AlarmRuntimeState(
                alarm_identity=current.alarm_identity,
                occurrence=occurrence,
                last_evaluation=evaluation,
                technical_hold=hold,
                management_cycle=current.management_cycle,
                management_effect=current.management_effect,
                deactivation_effect=current.deactivation_effect,
                assignments=current.assignments,
                pending_assignments=current.pending_assignments,
            ),
            technical_hold_changes=(started,),
        )
    if evaluation.evaluated_at < current.technical_hold.due_at:
        return _AlarmResolution(retained_state=current)
    closed = occurrence.close(
        ended_at=current.technical_hold.due_at,
        reason=OccurrenceClosureReason.TECHNICAL_HOLD_EXPIRED,
    )
    cleared = TechnicalHoldChange(
        kind=TechnicalHoldChangeKind.CLEARED,
        alarm_identity=current.alarm_identity,
        occurrence_id=occurrence.occurrence_id,
        effective_at=current.technical_hold.due_at,
    )
    return _AlarmResolution(
        retained_state=_management_only_state(current),
        closed_occurrence=closed,
        technical_hold_changes=(cleared,),
    )


def _management_only_state(current: AlarmRuntimeState) -> AlarmRuntimeState | None:
    if current.management_effect is None and current.deactivation_effect is None:
        return None
    return AlarmRuntimeState(
        alarm_identity=current.alarm_identity,
        management_effect=current.management_effect,
        deactivation_effect=current.deactivation_effect,
    )


def _clear_management_only_states(
    states: dict[AlarmIdentity, AlarmRuntimeState],
    *,
    effective_at: datetime,
    changes: list[ManagementEffectChange],
) -> None:
    for identity in sorted(tuple(states)):
        current = states[identity]
        if current.occurrence is not None or current.management_effect is None:
            continue
        changes.append(
            ManagementEffectChange(
                kind=ManagementEffectChangeKind.CLEARED,
                alarm_identity=identity,
                effective_at=effective_at,
            )
        )
        if current.deactivation_effect is None:
            del states[identity]
        else:
            states[identity] = AlarmRuntimeState(
                alarm_identity=identity,
                deactivation_effect=current.deactivation_effect,
            )


def _clear_hold_if_needed(
    current: AlarmRuntimeState,
    effective_at: datetime,
) -> tuple[TechnicalHoldChange, ...]:
    if current.technical_hold is None or current.occurrence is None:
        return ()
    return (
        TechnicalHoldChange(
            kind=TechnicalHoldChangeKind.CLEARED,
            alarm_identity=current.alarm_identity,
            occurrence_id=current.occurrence.occurrence_id,
            effective_at=effective_at,
        ),
    )


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


def _index_evaluations(
    cycle_at: datetime,
    evaluations: Sequence[AlarmEvaluation],
) -> dict[AlarmIdentity, AlarmEvaluation]:
    result: dict[AlarmIdentity, AlarmEvaluation] = {}
    for evaluation in evaluations:
        if not isinstance(evaluation, AlarmEvaluation):
            raise TypeError('evaluations must contain AlarmEvaluation values')
        if evaluation.evaluated_at != cycle_at:
            raise AlarmContractError('evaluation timestamp does not match frozen cycle time')
        if evaluation.alarm_identity in result:
            raise AlarmContractError('duplicate_evaluation')
        result[evaluation.alarm_identity] = evaluation
    return result


def _index_closures(
    cycle_at: datetime,
    closures: Sequence[ConfigurationClosure],
) -> dict[AlarmIdentity, ConfigurationClosure]:
    result: dict[AlarmIdentity, ConfigurationClosure] = {}
    for closure in closures:
        if not isinstance(closure, ConfigurationClosure):
            raise TypeError('configuration_closures must contain ConfigurationClosure values')
        if closure.effective_at != cycle_at:
            raise AlarmContractError('configuration closure timestamp does not match cycle time')
        if closure.alarm_identity in result:
            raise AlarmContractError('configuration_closures must not contain duplicate identities')
        result[closure.alarm_identity] = closure
    return result


def _validate_evaluation_cardinality(
    state: GroupLifecycleState,
    plans: dict[AlarmIdentity, PlannedAlarm],
    evaluations: dict[AlarmIdentity, AlarmEvaluation],
    closures: dict[AlarmIdentity, ConfigurationClosure],
) -> None:
    missing = set(plans) - set(evaluations)
    if missing:
        raise AlarmContractError('missing_evaluation')
    allowed_extra = {
        alarm.alarm_identity for alarm in state.alarms if alarm.alarm_identity in closures
    }
    unexpected = set(evaluations) - set(plans) - allowed_extra
    if unexpected:
        raise AlarmContractError('unexpected_evaluation')


def _episode_closure_reason(
    reasons: Sequence[OccurrenceClosureReason],
) -> EpisodeClosureReason:
    if reasons and all(
        reason is OccurrenceClosureReason.CONDITION_NORMALIZED for reason in reasons
    ):
        return EpisodeClosureReason.CONDITION_NORMALIZED
    if any(
        reason
        in {
            OccurrenceClosureReason.CONFIGURATION_DISABLED,
            OccurrenceClosureReason.CONFIGURATION_REMOVED,
            OccurrenceClosureReason.CONFIGURATION_RECONFIGURED,
        }
        for reason in reasons
    ):
        return EpisodeClosureReason.CONFIGURATION_TERMINATED
    return EpisodeClosureReason.TECHNICAL_UNCERTAINTY


def _has_open_occurrence(states: dict[AlarmIdentity, AlarmRuntimeState]) -> bool:
    return any(alarm.occurrence is not None for alarm in states.values())


def _occurrence_change_sort_key(
    change: OccurrenceChange,
) -> tuple[datetime, AlarmIdentity, str]:
    effective_at = (
        change.occurrence.started_at
        if change.kind is OccurrenceChangeKind.STARTED
        else change.occurrence.ended_at
    )
    if effective_at is None:
        raise AlarmLifecycleError('closed occurrence change requires ended_at')
    return effective_at, change.occurrence.alarm_identity, change.kind.value


def _episode_change_sort_key(change: EpisodeChange) -> tuple[datetime, str, str]:
    effective_at = (
        change.episode.started_at
        if change.kind is EpisodeChangeKind.STARTED
        else change.episode.ended_at
    )
    if effective_at is None:
        raise AlarmLifecycleError('closed episode change requires ended_at')
    return effective_at, change.episode.episode_id, change.kind.value


def _require_generated_id(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise AlarmLifecycleError(f'{name} factory must return a string')
    if not value.strip():
        raise AlarmLifecycleError(f'{name} factory must return a non-empty string')
    return value


def _validate_cycle_at(cycle_at: datetime) -> None:
    if not isinstance(cycle_at, datetime):
        raise TypeError('cycle_at must be a datetime')
    if cycle_at.tzinfo is None or cycle_at.utcoffset() != timedelta(0):
        raise ValueError('cycle_at must be timezone-aware UTC')


def _validate_grace(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError('technical_hold_grace_seconds must be an int')
    if value <= 0:
        raise ValueError('technical_hold_grace_seconds must be greater than zero')
