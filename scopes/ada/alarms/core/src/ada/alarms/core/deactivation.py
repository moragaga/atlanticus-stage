from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta

from ada.alarms.core.errors import AlarmContractError, AlarmLifecycleError
from ada.alarms.core.models import (
    AlarmIdentity,
    AlarmRuntimeState,
    DeactivationDecision,
    DeactivationDecisionKind,
    DeactivationDecisionOutcome,
    DeactivationDecisionResult,
    DeactivationEffect,
    DeactivationEffectChange,
    DeactivationEffectChangeKind,
    DeactivationRequest,
    DeactivationRequestOutcome,
    DeactivationRequestResult,
    ManagementAction,
    ManagementActionOutcome,
    ManagementActionResult,
    PlannedAlarm,
)

DeactivationRequestIdFactory = Callable[[ManagementAction], str]
DeactivationEffectIdFactory = Callable[[DeactivationRequest], str]


def is_deactivated(state: AlarmRuntimeState, *, at: datetime) -> bool:
    if not isinstance(state, AlarmRuntimeState):
        raise TypeError('state must be an AlarmRuntimeState')
    _require_utc_datetime(at, 'at')
    effect = state.deactivation_effect
    return bool(effect is not None and effect.effective_from <= at < effect.effective_until)


def _index_pending_requests(
    pending_requests: Sequence[DeactivationRequest],
) -> tuple[dict[str, DeactivationRequest], dict[AlarmIdentity, str]]:
    by_id: dict[str, DeactivationRequest] = {}
    by_alarm: dict[AlarmIdentity, str] = {}
    for request in pending_requests:
        if not isinstance(request, DeactivationRequest):
            raise TypeError('pending_deactivation_requests must contain DeactivationRequest values')
        if not request.approval_required:
            raise AlarmContractError('pending deactivation request must require approval')
        if request.request_id in by_id:
            raise AlarmContractError(
                'pending_deactivation_requests must not contain duplicate request_id'
            )
        if request.alarm_identity in by_alarm:
            raise AlarmContractError('only one pending deactivation request is allowed per alarm')
        by_id[request.request_id] = request
        by_alarm[request.alarm_identity] = request.request_id
    return by_id, by_alarm


def _validate_decisions(
    decisions: Sequence[DeactivationDecision],
    *,
    cycle_at: datetime,
) -> tuple[DeactivationDecision, ...]:
    seen: set[str] = set()
    result: list[DeactivationDecision] = []
    for decision in decisions:
        if not isinstance(decision, DeactivationDecision):
            raise TypeError('deactivation_decisions must contain DeactivationDecision values')
        if decision.decision_id in seen:
            raise AlarmContractError(
                'deactivation_decisions must not contain duplicate decision_id'
            )
        if decision.decided_at > cycle_at:
            raise AlarmContractError('deactivation decision decided_at must not be after cycle_at')
        seen.add(decision.decision_id)
        result.append(decision)
    return tuple(result)


def _expire_deactivation_effects(
    working: dict[AlarmIdentity, AlarmRuntimeState],
    *,
    cutoff: datetime,
    include_equal: bool,
) -> tuple[dict[AlarmIdentity, AlarmRuntimeState], tuple[DeactivationEffectChange, ...]]:
    changes: list[DeactivationEffectChange] = []
    for identity in sorted(tuple(working)):
        current = working[identity]
        effect = current.deactivation_effect
        if effect is None:
            continue
        if effect.effective_until > cutoff:
            continue
        if effect.effective_until == cutoff and not include_equal:
            continue
        next_state = replace(current, deactivation_effect=None)
        changes.append(
            DeactivationEffectChange(
                kind=DeactivationEffectChangeKind.CLEARED,
                alarm_identity=identity,
                effective_at=effect.effective_until,
            )
        )
        if next_state.occurrence is None and next_state.management_effect is None:
            del working[identity]
        else:
            working[identity] = next_state
    return working, tuple(changes)


def _apply_deactivation_action(
    working: dict[AlarmIdentity, AlarmRuntimeState],
    *,
    plans: Mapping[AlarmIdentity, PlannedAlarm],
    action_result: ManagementActionResult,
    pending_by_id: dict[str, DeactivationRequest],
    pending_by_alarm: dict[AlarmIdentity, str],
    request_id_factory: DeactivationRequestIdFactory | None,
    effect_id_factory: DeactivationEffectIdFactory | None,
) -> tuple[
    dict[AlarmIdentity, AlarmRuntimeState],
    DeactivationRequestResult | None,
    tuple[DeactivationEffectChange, ...],
]:
    action = action_result.action
    intent = action.deactivation_intent
    if intent is None:
        return working, None, ()
    if action_result.outcome is ManagementActionOutcome.LATE:
        return (
            working,
            DeactivationRequestResult(action=action, outcome=DeactivationRequestOutcome.LATE),
            (),
        )
    plan = plans.get(action.alarm_identity)
    if plan is None or plan.deactivation_policy is None:
        return (
            working,
            DeactivationRequestResult(
                action=action,
                outcome=DeactivationRequestOutcome.UNAVAILABLE,
            ),
            (),
        )
    current = working.get(action.alarm_identity)
    if current is None or current.occurrence is None:
        return (
            working,
            DeactivationRequestResult(action=action, outcome=DeactivationRequestOutcome.LATE),
            (),
        )
    if action.source_occurrence_id not in {None, current.occurrence.occurrence_id}:
        return (
            working,
            DeactivationRequestResult(action=action, outcome=DeactivationRequestOutcome.LATE),
            (),
        )
    if is_deactivated(current, at=action.source_created_at):
        return (
            working,
            DeactivationRequestResult(
                action=action,
                outcome=DeactivationRequestOutcome.ADDITIONAL,
            ),
            (),
        )
    if action.alarm_identity in pending_by_alarm:
        return (
            working,
            DeactivationRequestResult(
                action=action,
                outcome=DeactivationRequestOutcome.ADDITIONAL,
            ),
            (),
        )
    if request_id_factory is None:
        raise AlarmContractError(
            'deactivation_request_id_factory is required for deactivation intent'
        )
    request_id = _require_generated_id(request_id_factory(action), 'deactivation_request_id')
    if request_id in pending_by_id:
        raise AlarmLifecycleError('deactivation request factory returned a duplicate identifier')
    request = DeactivationRequest(
        request_id=request_id,
        alarm_identity=action.alarm_identity,
        source_management_input_id=action.input_id,
        source_occurrence_id=current.occurrence.occurrence_id,
        requested_at=action.source_created_at,
        effective_until=intent.effective_until,
        approval_required=plan.deactivation_policy.approval_required,
    )
    if request.approval_required:
        pending_by_id[request.request_id] = request
        pending_by_alarm[request.alarm_identity] = request.request_id
        return (
            working,
            DeactivationRequestResult(
                action=action,
                outcome=DeactivationRequestOutcome.PENDING_APPROVAL,
                deactivation_request=request,
            ),
            (),
        )
    effect = _create_effect(
        request=request,
        effective_from=request.requested_at,
        effect_id_factory=effect_id_factory,
    )
    working[action.alarm_identity] = replace(current, deactivation_effect=effect)
    return (
        working,
        DeactivationRequestResult(
            action=action,
            outcome=DeactivationRequestOutcome.DIRECT,
            deactivation_request=request,
            deactivation_effect_id=effect.effect_id,
        ),
        (
            DeactivationEffectChange(
                kind=DeactivationEffectChangeKind.STARTED,
                alarm_identity=action.alarm_identity,
                effective_at=effect.effective_from,
                deactivation_effect=effect,
            ),
        ),
    )


def _apply_deactivation_decision(
    working: dict[AlarmIdentity, AlarmRuntimeState],
    *,
    plans: Mapping[AlarmIdentity, PlannedAlarm],
    decision: DeactivationDecision,
    pending_by_id: dict[str, DeactivationRequest],
    pending_by_alarm: dict[AlarmIdentity, str],
    effect_id_factory: DeactivationEffectIdFactory | None,
) -> tuple[
    dict[AlarmIdentity, AlarmRuntimeState],
    DeactivationDecisionResult,
    tuple[DeactivationEffectChange, ...],
]:
    request = pending_by_id.get(decision.request_id)
    if request is None:
        return (
            working,
            DeactivationDecisionResult(
                decision=decision,
                outcome=DeactivationDecisionOutcome.PENDING_DEPENDENCY,
            ),
            (),
        )
    if decision.decided_at < request.requested_at:
        raise AlarmContractError('deactivation decision must not precede its request')
    if decision.kind is not DeactivationDecisionKind.APPROVED:
        _remove_pending(request, pending_by_id=pending_by_id, pending_by_alarm=pending_by_alarm)
        outcome = {
            DeactivationDecisionKind.REJECTED: DeactivationDecisionOutcome.REJECTED,
            DeactivationDecisionKind.CANCELLED: DeactivationDecisionOutcome.CANCELLED,
            DeactivationDecisionKind.INVALIDATED: DeactivationDecisionOutcome.INVALIDATED,
        }[decision.kind]
        return (
            working,
            DeactivationDecisionResult(
                decision=decision,
                outcome=outcome,
                deactivation_request=request,
            ),
            (),
        )
    _remove_pending(request, pending_by_id=pending_by_id, pending_by_alarm=pending_by_alarm)
    if request.effective_until <= decision.decided_at:
        return (
            working,
            DeactivationDecisionResult(
                decision=decision,
                outcome=DeactivationDecisionOutcome.EXPIRED,
                deactivation_request=request,
            ),
            (),
        )
    plan = plans.get(request.alarm_identity)
    if (
        plan is None
        or plan.deactivation_policy is None
        or not plan.deactivation_policy.approval_required
    ):
        return (
            working,
            DeactivationDecisionResult(
                decision=decision,
                outcome=DeactivationDecisionOutcome.INVALIDATED,
                deactivation_request=request,
            ),
            (),
        )
    current = working.get(request.alarm_identity)
    if (
        current is None
        or current.occurrence is None
        or current.occurrence.occurrence_id != request.source_occurrence_id
    ):
        return (
            working,
            DeactivationDecisionResult(
                decision=decision,
                outcome=DeactivationDecisionOutcome.STALE_TARGET,
                deactivation_request=request,
            ),
            (),
        )
    if is_deactivated(current, at=decision.decided_at):
        return (
            working,
            DeactivationDecisionResult(
                decision=decision,
                outcome=DeactivationDecisionOutcome.INVALIDATED,
                deactivation_request=request,
            ),
            (),
        )
    effect = _create_effect(
        request=request,
        effective_from=decision.decided_at,
        effect_id_factory=effect_id_factory,
    )
    working[request.alarm_identity] = replace(current, deactivation_effect=effect)
    return (
        working,
        DeactivationDecisionResult(
            decision=decision,
            outcome=DeactivationDecisionOutcome.APPLIED,
            deactivation_request=request,
            deactivation_effect_id=effect.effect_id,
        ),
        (
            DeactivationEffectChange(
                kind=DeactivationEffectChangeKind.STARTED,
                alarm_identity=request.alarm_identity,
                effective_at=effect.effective_from,
                deactivation_effect=effect,
            ),
        ),
    )


def _create_effect(
    *,
    request: DeactivationRequest,
    effective_from: datetime,
    effect_id_factory: DeactivationEffectIdFactory | None,
) -> DeactivationEffect:
    if effect_id_factory is None:
        raise AlarmContractError(
            'deactivation_effect_id_factory is required to materialize DeactivationEffect'
        )
    effect_id = _require_generated_id(effect_id_factory(request), 'deactivation_effect_id')
    return DeactivationEffect(
        effect_id=effect_id,
        effective_from=effective_from,
        effective_until=request.effective_until,
    )


def _remove_pending(
    request: DeactivationRequest,
    *,
    pending_by_id: dict[str, DeactivationRequest],
    pending_by_alarm: dict[AlarmIdentity, str],
) -> None:
    pending_by_id.pop(request.request_id, None)
    if pending_by_alarm.get(request.alarm_identity) == request.request_id:
        del pending_by_alarm[request.alarm_identity]


def _require_generated_id(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AlarmLifecycleError(f'{name} factory must return a non-empty string')
    return value


def _require_utc_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f'{name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f'{name} must be timezone-aware UTC')
