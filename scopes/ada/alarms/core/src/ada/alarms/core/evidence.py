from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from ada.alarms.core.errors import AlarmContractError
from ada.alarms.core.models import (
    AlarmEvaluation,
    AlarmIdentity,
    AlarmStatus,
    GroupLifecycleDecision,
    GroupLifecycleState,
    OccurrenceChangeKind,
    RuntimeEvaluationState,
    TechnicalHoldChangeKind,
)

DEFAULT_EVIDENCE_SAMPLING_INTERVAL_SECONDS = 300


@dataclass(frozen=True, slots=True)
class EvidenceContractRef:
    contract_key: str
    contract_version: str

    def __post_init__(self) -> None:
        _require_non_empty_string(self.contract_key, 'contract_key')
        _require_non_empty_string(self.contract_version, 'contract_version')


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    occurrence_id: str
    evaluation: AlarmEvaluation
    technical_contract: EvidenceContractRef | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.evidence_id, 'evidence_id')
        _require_non_empty_string(self.occurrence_id, 'occurrence_id')
        if not isinstance(self.evaluation, AlarmEvaluation):
            raise TypeError('evaluation must be an AlarmEvaluation')
        if self.evaluation.status is AlarmStatus.ERROR:
            if not isinstance(self.technical_contract, EvidenceContractRef):
                raise AlarmContractError('technical Evidence requires an explicit contract')
        elif self.technical_contract is not None:
            raise ValueError('physical Evidence must not contain technical_contract')

    @property
    def alarm_identity(self) -> AlarmIdentity:
        return self.evaluation.alarm_identity

    @property
    def recorded_at(self) -> datetime:
        return self.evaluation.evaluated_at

    def as_document(self) -> dict[str, Any]:
        snapshot = self.evaluation.evidence_snapshot
        if snapshot is not None:
            contract_key = snapshot.contract_key
            contract_version = snapshot.contract_version
            payload = _json_value(snapshot.payload)
        else:
            error = self.evaluation.error
            if error is None or self.technical_contract is None:
                raise AlarmContractError(
                    'technical Evidence requires evaluation error and contract'
                )
            contract_key = self.technical_contract.contract_key
            contract_version = self.technical_contract.contract_version
            payload = {
                'error': {
                    'origin': error.origin.value,
                    'error_key': error.error_key,
                    'message': error.message,
                    'affected_inputs': [
                        {
                            'reason_key': issue.reason_key,
                            **(
                                {'source_key': issue.source_key}
                                if issue.source_key is not None
                                else {}
                            ),
                            **(
                                {'scope_key': issue.scope_key}
                                if issue.scope_key is not None
                                else {}
                            ),
                            **(
                                {'resource_key': issue.resource_key}
                                if issue.resource_key is not None
                                else {}
                            ),
                            **({'fields': list(issue.fields)} if issue.fields else {}),
                        }
                        for issue in error.affected_inputs
                    ],
                }
            }
        return {
            'evidence_id': self.evidence_id,
            'alarm_key': self.alarm_identity.canonical_key,
            'occurrence_id': self.occurrence_id,
            'recorded_at': _timestamp(self.recorded_at),
            'evaluated_at': _timestamp(self.evaluation.evaluated_at),
            'status': self.evaluation.status.value,
            'contract_key': contract_key,
            'contract_version': contract_version,
            'payload': payload,
        }


@dataclass(frozen=True, slots=True)
class EvidenceMaterialization:
    state: GroupLifecycleState
    records: tuple[EvidenceRecord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.state, GroupLifecycleState):
            raise TypeError('state must be a GroupLifecycleState')
        if not isinstance(self.records, tuple):
            raise TypeError('records must be a tuple')
        if not all(isinstance(record, EvidenceRecord) for record in self.records):
            raise TypeError('records must contain EvidenceRecord values')
        ids = [record.evidence_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError('records must not contain duplicate evidence_id values')


def materialize_evidence(
    previous_state: GroupLifecycleState,
    decision: GroupLifecycleDecision,
    *,
    evaluations: Mapping[AlarmIdentity, AlarmEvaluation],
    evidence_sampling_interval_seconds: int = DEFAULT_EVIDENCE_SAMPLING_INTERVAL_SECONDS,
    technical_evidence_contract: EvidenceContractRef | None = None,
) -> EvidenceMaterialization:
    if not isinstance(previous_state, GroupLifecycleState):
        raise TypeError('previous_state must be a GroupLifecycleState')
    if not isinstance(decision, GroupLifecycleDecision):
        raise TypeError('decision must be a GroupLifecycleDecision')
    if previous_state.priority_group != decision.state.priority_group:
        raise AlarmContractError('previous and next state priority_group must match')
    _validate_sampling_interval(evidence_sampling_interval_seconds)
    if technical_evidence_contract is not None and not isinstance(
        technical_evidence_contract, EvidenceContractRef
    ):
        raise TypeError('technical_evidence_contract must be an EvidenceContractRef')
    working = {alarm.alarm_identity: alarm for alarm in decision.state.alarms}
    evidence: list[EvidenceRecord] = []
    interval = timedelta(seconds=evidence_sampling_interval_seconds)
    started = {
        change.occurrence.alarm_identity: change.occurrence
        for change in decision.occurrence_changes
        if change.kind is OccurrenceChangeKind.STARTED
    }
    closed = {
        change.occurrence.alarm_identity: change.occurrence
        for change in decision.occurrence_changes
        if change.kind is OccurrenceChangeKind.CLOSED
    }
    hold_started = {
        change.alarm_identity
        for change in decision.technical_hold_changes
        if change.kind is TechnicalHoldChangeKind.STARTED
    }
    hold_cleared = {
        change.alarm_identity
        for change in decision.technical_hold_changes
        if change.kind is TechnicalHoldChangeKind.CLEARED
    }
    for identity, evaluation in sorted(evaluations.items()):
        previous = previous_state.get(identity)
        current = working.get(identity)
        if identity in started:
            occurrence = started[identity]
            evidence.append(
                EvidenceRecord(
                    evidence_id=_evidence_id(occurrence.occurrence_id, 'initial'),
                    occurrence_id=occurrence.occurrence_id,
                    evaluation=evaluation,
                    technical_contract=(
                        technical_evidence_contract
                        if evaluation.status is AlarmStatus.ERROR
                        else None
                    ),
                )
            )
            if current is None or current.occurrence is None:
                raise AlarmContractError('started occurrence requires runtime state')
            working[identity] = replace(
                current,
                next_evidence_due_at=evaluation.evaluated_at + interval,
            )
            continue
        if identity in closed:
            occurrence = closed[identity]
            if occurrence.ended_at != evaluation.evaluated_at:
                continue
            if evaluation.status in {AlarmStatus.ACTIVE, AlarmStatus.INACTIVE}:
                discriminator = f'final:{_timestamp(evaluation.evaluated_at)}'
            elif evaluation.status is AlarmStatus.ERROR:
                discriminator = f'technical:{_timestamp(evaluation.evaluated_at)}'
            else:
                continue
            evidence.append(
                EvidenceRecord(
                    evidence_id=_evidence_id(occurrence.occurrence_id, discriminator),
                    occurrence_id=occurrence.occurrence_id,
                    evaluation=evaluation,
                    technical_contract=(
                        technical_evidence_contract
                        if evaluation.status is AlarmStatus.ERROR
                        else None
                    ),
                )
            )
            continue
        if previous is None or previous.occurrence is None or current is None:
            continue
        occurrence_id = previous.occurrence.occurrence_id
        if identity in hold_started:
            evidence.append(
                EvidenceRecord(
                    evidence_id=_evidence_id(
                        occurrence_id,
                        f'technical:{_timestamp(evaluation.evaluated_at)}',
                    ),
                    occurrence_id=occurrence_id,
                    evaluation=evaluation,
                    technical_contract=technical_evidence_contract,
                )
            )
            if current.occurrence is not None:
                working[identity] = replace(current, next_evidence_due_at=None)
            continue
        if identity in hold_cleared and evaluation.status is AlarmStatus.ACTIVE:
            evidence.append(
                EvidenceRecord(
                    evidence_id=_evidence_id(
                        occurrence_id,
                        f'recovery:{_timestamp(evaluation.evaluated_at)}',
                    ),
                    occurrence_id=occurrence_id,
                    evaluation=evaluation,
                )
            )
            working[identity] = replace(
                current,
                next_evidence_due_at=evaluation.evaluated_at + interval,
            )
            continue
        if (
            evaluation.status is AlarmStatus.ACTIVE
            and current.technical_hold is None
            and previous.next_evidence_due_at is not None
            and evaluation.evaluated_at >= previous.next_evidence_due_at
        ):
            evidence.append(
                EvidenceRecord(
                    evidence_id=_evidence_id(
                        occurrence_id,
                        f'periodic:{_timestamp(previous.next_evidence_due_at)}',
                    ),
                    occurrence_id=occurrence_id,
                    evaluation=evaluation,
                )
            )
            working[identity] = replace(
                current,
                last_evaluation=RuntimeEvaluationState.from_evaluation(evaluation),
                next_evidence_due_at=evaluation.evaluated_at + interval,
            )
    state = GroupLifecycleState(
        priority_group=decision.state.priority_group,
        episode=decision.state.episode,
        alarms=tuple(working[identity] for identity in sorted(working)),
    )
    records = tuple(sorted(evidence, key=lambda item: (item.recorded_at, item.evidence_id)))
    return EvidenceMaterialization(state=state, records=records)


def _evidence_id(occurrence_id: str, discriminator: str) -> str:
    return f'evidence:{occurrence_id}:{discriminator}'


def _timestamp(value: datetime) -> str:
    _require_utc_datetime(value, 'timestamp')
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TypeError('Evidence payload must contain JSON-compatible values')


def _validate_sampling_interval(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError('evidence_sampling_interval_seconds must be an int')
    if value <= 0:
        raise ValueError('evidence_sampling_interval_seconds must be greater than zero')


def _require_non_empty_string(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f'{name} must be a str')
    if not value.strip():
        raise ValueError(f'{name} must not be empty')


def _require_utc_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f'{name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f'{name} must be UTC')
