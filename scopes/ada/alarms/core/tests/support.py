from __future__ import annotations

from datetime import UTC, datetime

from ada.alarms.core import (
    AlarmEvaluation,
    AlarmIdentity,
    AlarmKind,
    AlarmStatus,
    Criticality,
    EvaluationError,
    EvaluationErrorOrigin,
    EvidenceSnapshot,
    PlannedAlarm,
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
    alarm_key: str, *, at: datetime = NOW, error_key: str = 'insufficient_data'
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


class Ids:
    def __init__(self) -> None:
        self.occurrence = 0
        self.episode = 0

    def new_occurrence(self, _identity: AlarmIdentity, _at: datetime) -> str:
        self.occurrence += 1
        return f'O{self.occurrence}'

    def new_episode(self, _priority_group: str, _at: datetime) -> str:
        self.episode += 1
        return f'E{self.episode}'
