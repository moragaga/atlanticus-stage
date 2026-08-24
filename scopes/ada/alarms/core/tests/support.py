from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ada.alarms.core import (
    AlarmEvaluation,
    AlarmIdentity,
    AlarmKind,
    AlarmRouting,
    AlarmStatus,
    Criticality,
    DeactivationDecision,
    DeactivationDecisionKind,
    DeactivationIntent,
    DeactivationPolicy,
    DeactivationRequest,
    EvaluationError,
    EvaluationErrorOrigin,
    EvidenceSnapshot,
    ManagementAction,
    PlannedAlarm,
    RoutingDestination,
)

NOW = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)


def identity(alarm_key: str = 'risk') -> AlarmIdentity:
    return AlarmIdentity(family_key='mill', alarm_key=alarm_key)


def plan(
    alarm_key: str = 'risk',
    *,
    kind: AlarmKind = AlarmKind.RISK,
    criticality: Criticality = Criticality.C2,
    priority_order: int = 2,
    delivery_enabled: bool = True,
    origin_tool_key: str = 'tool-a',
    destinations: tuple[RoutingDestination, ...] | None = None,
    deactivation_approval_required: bool | None = None,
) -> PlannedAlarm:
    return PlannedAlarm(
        identity=identity(alarm_key),
        kind=kind,
        criticality=criticality,
        priority_group='mill-feed',
        priority_order=priority_order,
        delivery_enabled=delivery_enabled,
        evaluator_key='threshold',
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        routing=AlarmRouting(
            origin_tool_key=origin_tool_key,
            destinations=(
                destinations
                if destinations is not None
                else (
                    ()
                    if criticality is Criticality.C3
                    else (
                        RoutingDestination(
                            tool_key='tool-b',
                            delay_seconds=(900 if criticality is Criticality.C2 else None),
                        ),
                    )
                )
            ),
        ),
        deactivation_policy=(
            None
            if deactivation_approval_required is None
            else DeactivationPolicy(approval_required=deactivation_approval_required)
        ),
    )


def physical(
    alarm_key: str,
    status: AlarmStatus,
    *,
    at: datetime = NOW,
) -> AlarmEvaluation:
    return AlarmEvaluation(
        alarm_identity=identity(alarm_key),
        status=status,
        evaluated_at=at,
        evidence_snapshot=EvidenceSnapshot(
            contract_key='threshold',
            contract_version='v1',
            payload={'value': 10.0},
        ),
    )


def error(
    alarm_key: str,
    *,
    at: datetime = NOW,
    error_key: str = 'insufficient_data',
) -> AlarmEvaluation:
    return AlarmEvaluation(
        alarm_identity=identity(alarm_key),
        status=AlarmStatus.ERROR,
        evaluated_at=at,
        error=EvaluationError(
            origin=EvaluationErrorOrigin.QUALITY,
            error_key=error_key,
            message='Input quality is insufficient',
        ),
    )


def management_action(
    alarm_key: str = 'risk',
    *,
    input_id: str = 'M1',
    occurrence_id: str | None = 'O1',
    at: datetime = NOW,
    tool_key: str = 'tool-a',
    actor_key: str = 'user-a',
    deactivation_until: datetime | None = None,
) -> ManagementAction:
    return ManagementAction(
        input_id=input_id,
        alarm_identity=identity(alarm_key),
        source_occurrence_id=occurrence_id,
        tool_key=tool_key,
        actor_key=actor_key,
        source_created_at=at,
        context={'channel': 'operator'},
        deactivation_intent=(
            None
            if deactivation_until is None
            else DeactivationIntent(effective_until=deactivation_until)
        ),
    )


def deactivation_request(
    alarm_key: str = 'risk',
    *,
    request_id: str = 'DR1',
    management_input_id: str = 'M1',
    occurrence_id: str = 'O1',
    requested_at: datetime = NOW,
    effective_until: datetime | None = None,
) -> DeactivationRequest:
    return DeactivationRequest(
        request_id=request_id,
        alarm_identity=identity(alarm_key),
        source_management_input_id=management_input_id,
        source_occurrence_id=occurrence_id,
        requested_at=requested_at,
        effective_until=(
            requested_at + timedelta(hours=1) if effective_until is None else effective_until
        ),
        approval_required=True,
    )


def deactivation_decision(
    request_id: str = 'DR1',
    *,
    decision_id: str = 'DD1',
    kind: DeactivationDecisionKind = DeactivationDecisionKind.APPROVED,
    at: datetime = NOW,
    actor_key: str = 'approver-a',
) -> DeactivationDecision:
    return DeactivationDecision(
        decision_id=decision_id,
        request_id=request_id,
        kind=kind,
        decided_at=at,
        actor_key=actor_key,
    )


def reappear_after(seconds: int):
    def resolve(action: ManagementAction) -> datetime:
        return action.source_created_at + timedelta(seconds=seconds)

    return resolve


class Ids:
    def __init__(self) -> None:
        self.occurrence = 0
        self.episode = 0
        self.management_effect = 0
        self.deactivation_request = 0
        self.deactivation_effect = 0

    def new_occurrence(self, _identity: AlarmIdentity, _at: datetime) -> str:
        self.occurrence += 1
        return f'O{self.occurrence}'

    def new_episode(self, _priority_group: str, _at: datetime) -> str:
        self.episode += 1
        return f'E{self.episode}'

    def new_management_effect(self, _action: ManagementAction) -> str:
        self.management_effect += 1
        return f'ME{self.management_effect}'

    def new_deactivation_request(self, _action: ManagementAction) -> str:
        self.deactivation_request += 1
        return f'DR{self.deactivation_request}'

    def new_deactivation_effect(self, _request: DeactivationRequest) -> str:
        self.deactivation_effect += 1
        return f'DE{self.deactivation_effect}'
