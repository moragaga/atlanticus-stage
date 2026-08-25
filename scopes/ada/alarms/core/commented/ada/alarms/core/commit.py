# Espejo pedagógico: convierte una GroupLifecycleDecision en un EngineCommit lógico y records inmutables.
# Este módulo no escribe WAL ni snapshots; esa frontera física pertenece a Alarm Persistence.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from ada.alarms.core.errors import AlarmContractError
from ada.alarms.core.evidence import (
    DEFAULT_EVIDENCE_SAMPLING_INTERVAL_SECONDS,
    EvidenceContractRef,
    EvidenceRecord,
    materialize_evidence,
)
from ada.alarms.core.journey import JourneyEvent, materialize_journey
from ada.alarms.core.models import (
    AlarmEvaluation,
    AlarmIdentity,
    AssignmentChange,
    AssignmentChangeKind,
    DeactivationDecisionOutcome,
    DeactivationEffectChangeKind,
    DeactivationRequest,
    EpisodeChange,
    GroupLifecycleDecision,
    GroupLifecycleState,
    GroupPriorityResolution,
    ManagementEffectChangeKind,
    OccurrenceChange,
    OccurrenceChangeKind,
)


# Clase InputKind: contrato tipado con invariantes explícitas para evitar estados ambiguos.
class InputKind(StrEnum):
    MANAGEMENT = 'MANAGEMENT'
    DEACTIVATION_REQUEST = 'DEACTIVATION_REQUEST'
    DEACTIVATION_DECISION = 'DEACTIVATION_DECISION'


@dataclass(frozen=True, slots=True)
# Clase InputReceipt: contrato tipado con invariantes explícitas para evitar estados ambiguos.
class InputReceipt:
    input_id: str
    input_kind: InputKind
    commit_id: str
    applied_at: datetime
    outcome: str

    def __post_init__(self) -> None:
        _require_non_empty_string(self.input_id, 'input_id')
        if not isinstance(self.input_kind, InputKind):
            raise TypeError('input_kind must be an InputKind')
        _require_non_empty_string(self.commit_id, 'commit_id')
        _require_utc_datetime(self.applied_at, 'applied_at')
        _require_non_empty_string(self.outcome, 'outcome')

    @property
    def receipt_id(self) -> str:
        return f'{self.input_kind.value}:{self.input_id}'

    def as_document(self) -> dict[str, Any]:
        return {
            'input_id': self.input_id,
            'input_kind': self.input_kind.value,
            'commit_id': self.commit_id,
            'applied_at': _timestamp(self.applied_at),
            'outcome': self.outcome,
        }


@dataclass(frozen=True, slots=True)
# Clase ManagementEffectRecord: contrato tipado con invariantes explícitas para evitar estados ambiguos.
class ManagementEffectRecord:
    record_id: str
    effect_id: str
    kind: ManagementEffectChangeKind
    alarm_identity: AlarmIdentity
    effective_at: datetime
    source_occurrence_id: str
    effect_effective_at: datetime
    reappearance_due_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty_string(self.record_id, 'record_id')
        _require_non_empty_string(self.effect_id, 'effect_id')
        if not isinstance(self.kind, ManagementEffectChangeKind):
            raise TypeError('kind must be a ManagementEffectChangeKind')
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        _require_utc_datetime(self.effective_at, 'effective_at')
        _require_non_empty_string(self.source_occurrence_id, 'source_occurrence_id')
        _require_utc_datetime(self.effect_effective_at, 'effect_effective_at')
        _require_utc_datetime(self.reappearance_due_at, 'reappearance_due_at')

    def as_document(self) -> dict[str, Any]:
        return {
            'record_id': self.record_id,
            'effect_id': self.effect_id,
            'kind': self.kind.value,
            'alarm_key': self.alarm_identity.canonical_key,
            'effective_at': _timestamp(self.effective_at),
            'source_occurrence_id': self.source_occurrence_id,
            'effect_effective_at': _timestamp(self.effect_effective_at),
            'reappearance_due_at': _timestamp(self.reappearance_due_at),
        }


@dataclass(frozen=True, slots=True)
# DeactivationRequestRecord conserva la solicitud exacta que nació del ManagementAction.
# Cruza la misma frontera durable que su InputReceipt para que recovery no tenga que recrearla.
class DeactivationRequestRecord:
    request_id: str
    alarm_identity: AlarmIdentity
    source_management_input_id: str
    source_occurrence_id: str
    requested_at: datetime
    effective_until: datetime
    approval_required: bool

    def __post_init__(self) -> None:
        _require_non_empty_string(self.request_id, 'request_id')
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        _require_non_empty_string(self.source_management_input_id, 'source_management_input_id')
        _require_non_empty_string(self.source_occurrence_id, 'source_occurrence_id')
        _require_utc_datetime(self.requested_at, 'requested_at')
        _require_utc_datetime(self.effective_until, 'effective_until')
        if self.effective_until <= self.requested_at:
            raise ValueError('effective_until must be after requested_at')
        if not isinstance(self.approval_required, bool):
            raise TypeError('approval_required must be a bool')

    @classmethod
    def from_request(cls, request: DeactivationRequest) -> DeactivationRequestRecord:
        if not isinstance(request, DeactivationRequest):
            raise TypeError('request must be a DeactivationRequest')
        return cls(
            request_id=request.request_id,
            alarm_identity=request.alarm_identity,
            source_management_input_id=request.source_management_input_id,
            source_occurrence_id=request.source_occurrence_id,
            requested_at=request.requested_at,
            effective_until=request.effective_until,
            approval_required=request.approval_required,
        )

    def as_document(self) -> dict[str, Any]:
        return {
            'request_id': self.request_id,
            'alarm_key': self.alarm_identity.canonical_key,
            'source_management_input_id': self.source_management_input_id,
            'source_occurrence_id': self.source_occurrence_id,
            'requested_at': _timestamp(self.requested_at),
            'effective_until': _timestamp(self.effective_until),
            'approval_required': self.approval_required,
        }


@dataclass(frozen=True, slots=True)
# Clase DeactivationEffectRecord: contrato tipado con invariantes explícitas para evitar estados ambiguos.
class DeactivationEffectRecord:
    record_id: str
    effect_id: str
    kind: DeactivationEffectChangeKind
    alarm_identity: AlarmIdentity
    effective_at: datetime
    effective_from: datetime
    effective_until: datetime

    def __post_init__(self) -> None:
        _require_non_empty_string(self.record_id, 'record_id')
        _require_non_empty_string(self.effect_id, 'effect_id')
        if not isinstance(self.kind, DeactivationEffectChangeKind):
            raise TypeError('kind must be a DeactivationEffectChangeKind')
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        _require_utc_datetime(self.effective_at, 'effective_at')
        _require_utc_datetime(self.effective_from, 'effective_from')
        _require_utc_datetime(self.effective_until, 'effective_until')
        if self.effective_until <= self.effective_from:
            raise ValueError('effective_until must be after effective_from')

    def as_document(self) -> dict[str, Any]:
        return {
            'record_id': self.record_id,
            'effect_id': self.effect_id,
            'kind': self.kind.value,
            'alarm_key': self.alarm_identity.canonical_key,
            'effective_at': _timestamp(self.effective_at),
            'effective_from': _timestamp(self.effective_from),
            'effective_until': _timestamp(self.effective_until),
        }


@dataclass(frozen=True, slots=True)
# Clase AssignmentChangeRecord: contrato tipado con invariantes explícitas para evitar estados ambiguos.
class AssignmentChangeRecord:
    change_id: str
    occurrence_id: str
    kind: AssignmentChangeKind
    alarm_identity: AlarmIdentity
    tool_key: str
    effective_at: datetime
    due_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.change_id, 'change_id')
        _require_non_empty_string(self.occurrence_id, 'occurrence_id')
        if not isinstance(self.kind, AssignmentChangeKind):
            raise TypeError('kind must be an AssignmentChangeKind')
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        _require_non_empty_string(self.tool_key, 'tool_key')
        _require_utc_datetime(self.effective_at, 'effective_at')
        if self.due_at is not None:
            _require_utc_datetime(self.due_at, 'due_at')

    def as_document(self) -> dict[str, Any]:
        return {
            'change_id': self.change_id,
            'occurrence_id': self.occurrence_id,
            'kind': self.kind.value,
            'alarm_key': self.alarm_identity.canonical_key,
            'tool_key': self.tool_key,
            'effective_at': _timestamp(self.effective_at),
            'due_at': None if self.due_at is None else _timestamp(self.due_at),
        }


@dataclass(frozen=True, slots=True)
# Clase OccurrenceChangeReference: contrato tipado con invariantes explícitas para evitar estados ambiguos.
class OccurrenceChangeReference:
    occurrence_id: str
    alarm_identity: AlarmIdentity
    kind: OccurrenceChangeKind

    def __post_init__(self) -> None:
        _require_non_empty_string(self.occurrence_id, 'occurrence_id')
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        if not isinstance(self.kind, OccurrenceChangeKind):
            raise TypeError('kind must be an OccurrenceChangeKind')

    def as_document(self) -> dict[str, str]:
        return {
            'occurrence_id': self.occurrence_id,
            'alarm_key': self.alarm_identity.canonical_key,
            'kind': self.kind.value,
        }


@dataclass(frozen=True, slots=True)
# Clase EpisodeChangeReference: contrato tipado con invariantes explícitas para evitar estados ambiguos.
class EpisodeChangeReference:
    episode_id: str
    kind: str

    def __post_init__(self) -> None:
        _require_non_empty_string(self.episode_id, 'episode_id')
        _require_non_empty_string(self.kind, 'kind')

    def as_document(self) -> dict[str, str]:
        return {'episode_id': self.episode_id, 'kind': self.kind}


@dataclass(frozen=True, slots=True)
# Clase EngineCommit: contrato tipado con invariantes explícitas para evitar estados ambiguos.
class EngineCommit:
    commit_id: str
    cycle_id: str
    priority_group: str
    previous_commit_id: str | None
    evaluated_at: datetime
    committed_at: datetime
    alarm_configuration_revision: str
    tool_registry_revision: str
    runtime_artifact_version: str
    affected_alarms: tuple[AlarmIdentity, ...]
    runtime_state_updates: tuple[AlarmIdentity, ...]
    occurrence_changes: tuple[OccurrenceChangeReference, ...]
    episode_change: EpisodeChangeReference | None
    journey_event_ids: tuple[str, ...]
    evidence_record_ids: tuple[str, ...]
    management_effect_ids: tuple[str, ...]
    deactivation_request_ids: tuple[str, ...]
    deactivation_effect_ids: tuple[str, ...]
    assignment_change_ids: tuple[str, ...]
    receipt_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            'commit_id',
            'cycle_id',
            'priority_group',
            'alarm_configuration_revision',
            'tool_registry_revision',
            'runtime_artifact_version',
        ):
            _require_non_empty_string(getattr(self, name), name)
        if self.previous_commit_id is not None:
            _require_non_empty_string(self.previous_commit_id, 'previous_commit_id')
            if self.previous_commit_id == self.commit_id:
                raise ValueError('previous_commit_id must differ from commit_id')
        _require_utc_datetime(self.evaluated_at, 'evaluated_at')
        _require_utc_datetime(self.committed_at, 'committed_at')
        if self.committed_at < self.evaluated_at:
            raise ValueError('committed_at must not be before evaluated_at')
        if self.commit_id != commit_id_for(self.cycle_id, self.priority_group):
            raise ValueError('commit_id must equal cycle_id + priority_group')
        _require_unique_tuple(self.affected_alarms, AlarmIdentity, 'affected_alarms')
        if not self.affected_alarms:
            raise ValueError('affected_alarms must not be empty')
        _require_unique_tuple(
            self.runtime_state_updates,
            AlarmIdentity,
            'runtime_state_updates',
        )
        _require_typed_tuple(
            self.occurrence_changes,
            OccurrenceChangeReference,
            'occurrence_changes',
        )
        if self.episode_change is not None and not isinstance(
            self.episode_change, EpisodeChangeReference
        ):
            raise TypeError('episode_change must be an EpisodeChangeReference')
        for name in (
            'journey_event_ids',
            'evidence_record_ids',
            'management_effect_ids',
            'deactivation_request_ids',
            'deactivation_effect_ids',
            'assignment_change_ids',
            'receipt_ids',
        ):
            _require_unique_string_tuple(getattr(self, name), name)

    def as_document(self) -> dict[str, Any]:
        return {
            'commit_id': self.commit_id,
            'cycle_id': self.cycle_id,
            'priority_group': self.priority_group,
            'previous_commit_id': self.previous_commit_id,
            'evaluated_at': _timestamp(self.evaluated_at),
            'committed_at': _timestamp(self.committed_at),
            'alarm_configuration_revision': self.alarm_configuration_revision,
            'tool_registry_revision': self.tool_registry_revision,
            'runtime_artifact_version': self.runtime_artifact_version,
            'affected_alarms': [identity.canonical_key for identity in self.affected_alarms],
            'runtime_state_updates': [
                identity.canonical_key for identity in self.runtime_state_updates
            ],
            'occurrence_changes': [change.as_document() for change in self.occurrence_changes],
            'episode_change': (
                None if self.episode_change is None else self.episode_change.as_document()
            ),
            'journey_event_ids': list(self.journey_event_ids),
            'evidence_record_ids': list(self.evidence_record_ids),
            'management_effect_ids': list(self.management_effect_ids),
            'deactivation_request_ids': list(self.deactivation_request_ids),
            'deactivation_effect_ids': list(self.deactivation_effect_ids),
            'assignment_change_ids': list(self.assignment_change_ids),
            'receipt_ids': list(self.receipt_ids),
        }


@dataclass(frozen=True, slots=True)
# Clase EngineCommitRecords: contrato tipado con invariantes explícitas para evitar estados ambiguos.
class EngineCommitRecords:
    occurrence_changes: tuple[OccurrenceChange, ...] = ()
    episode_changes: tuple[EpisodeChange, ...] = ()
    journey_events: tuple[JourneyEvent, ...] = ()
    evidence_records: tuple[EvidenceRecord, ...] = ()
    management_effects: tuple[ManagementEffectRecord, ...] = ()
    deactivation_requests: tuple[DeactivationRequestRecord, ...] = ()
    deactivation_effects: tuple[DeactivationEffectRecord, ...] = ()
    assignment_changes: tuple[AssignmentChangeRecord, ...] = ()
    input_receipts: tuple[InputReceipt, ...] = ()

    def __post_init__(self) -> None:
        _require_typed_tuple(self.occurrence_changes, OccurrenceChange, 'occurrence_changes')
        _require_typed_tuple(self.episode_changes, EpisodeChange, 'episode_changes')
        _require_typed_tuple(self.journey_events, JourneyEvent, 'journey_events')
        _require_typed_tuple(self.evidence_records, EvidenceRecord, 'evidence_records')
        _require_typed_tuple(
            self.management_effects,
            ManagementEffectRecord,
            'management_effects',
        )
        _require_typed_tuple(
            self.deactivation_requests,
            DeactivationRequestRecord,
            'deactivation_requests',
        )
        _require_typed_tuple(
            self.deactivation_effects,
            DeactivationEffectRecord,
            'deactivation_effects',
        )
        _require_typed_tuple(self.assignment_changes, AssignmentChangeRecord, 'assignment_changes')
        _require_typed_tuple(self.input_receipts, InputReceipt, 'input_receipts')
        _require_unique_ids(self.journey_events, 'event_id', 'journey_events')
        _require_unique_ids(self.evidence_records, 'evidence_id', 'evidence_records')
        _require_unique_ids(self.management_effects, 'record_id', 'management_effects')
        _require_unique_ids(self.deactivation_requests, 'request_id', 'deactivation_requests')
        _require_unique_ids(self.deactivation_effects, 'record_id', 'deactivation_effects')
        _require_unique_ids(self.assignment_changes, 'change_id', 'assignment_changes')
        _require_unique_ids(self.input_receipts, 'receipt_id', 'input_receipts')

    def as_document(self) -> dict[str, list[dict[str, Any]]]:
        document: dict[str, list[dict[str, Any]]] = {}
        if self.occurrence_changes:
            document['occurrence_changes'] = [
                _occurrence_change_document(change) for change in self.occurrence_changes
            ]
        if self.episode_changes:
            document['episode_changes'] = [
                _episode_change_document(change) for change in self.episode_changes
            ]
        if self.journey_events:
            document['journey_events'] = [event.as_document() for event in self.journey_events]
        if self.evidence_records:
            document['evidence_records'] = [
                record.as_document() for record in self.evidence_records
            ]
        if self.management_effects:
            document['management_effects'] = [
                record.as_document() for record in self.management_effects
            ]
        if self.deactivation_requests:
            document['deactivation_requests'] = [
                record.as_document() for record in self.deactivation_requests
            ]
        if self.deactivation_effects:
            document['deactivation_effects'] = [
                record.as_document() for record in self.deactivation_effects
            ]
        if self.assignment_changes:
            document['assignment_changes'] = [
                record.as_document() for record in self.assignment_changes
            ]
        if self.input_receipts:
            document['input_receipts'] = [receipt.as_document() for receipt in self.input_receipts]
        return document


@dataclass(frozen=True, slots=True)
# Clase GroupCommitMaterialization: contrato tipado con invariantes explícitas para evitar estados ambiguos.
class GroupCommitMaterialization:
    state: GroupLifecycleState
    commit: EngineCommit
    records: EngineCommitRecords

    def __post_init__(self) -> None:
        if not isinstance(self.state, GroupLifecycleState):
            raise TypeError('state must be a GroupLifecycleState')
        if not isinstance(self.commit, EngineCommit):
            raise TypeError('commit must be an EngineCommit')
        if not isinstance(self.records, EngineCommitRecords):
            raise TypeError('records must be EngineCommitRecords')
        if self.state.priority_group != self.commit.priority_group:
            raise ValueError('state priority_group must match commit priority_group')


# Función cycle_id_for: transformación determinística; sus efectos externos se componen fuera del Core.
def cycle_id_for(evaluated_at: datetime) -> str:
    _require_utc_datetime(evaluated_at, 'evaluated_at')
    return evaluated_at.astimezone(UTC).strftime('%Y%m%dT%H%M%S%fZ')


# Función commit_id_for: transformación determinística; sus efectos externos se componen fuera del Core.
def commit_id_for(cycle_id: str, priority_group: str) -> str:
    _require_non_empty_string(cycle_id, 'cycle_id')
    _require_non_empty_string(priority_group, 'priority_group')
    return f'{cycle_id}__{priority_group}'


# Función materialize_group_commit: transformación determinística; sus efectos externos se componen fuera del Core.
def materialize_group_commit(
    previous_state: GroupLifecycleState,
    decision: GroupLifecycleDecision,
    *,
    evaluations: Sequence[AlarmEvaluation],
    cycle_at: datetime,
    committed_at: datetime,
    alarm_configuration_revision: str,
    tool_registry_revision: str,
    runtime_artifact_version: str,
    previous_commit_id: str | None = None,
    evidence_sampling_interval_seconds: int = DEFAULT_EVIDENCE_SAMPLING_INTERVAL_SECONDS,
    technical_evidence_contract: EvidenceContractRef | None = None,
    previous_priority_resolution: GroupPriorityResolution | None = None,
) -> GroupCommitMaterialization | None:
    if not isinstance(previous_state, GroupLifecycleState):
        raise TypeError('previous_state must be a GroupLifecycleState')
    if not isinstance(decision, GroupLifecycleDecision):
        raise TypeError('decision must be a GroupLifecycleDecision')
    if previous_state.priority_group != decision.state.priority_group:
        raise AlarmContractError('previous and next state priority_group must match')
    _require_utc_datetime(cycle_at, 'cycle_at')
    _require_utc_datetime(committed_at, 'committed_at')
    if committed_at < cycle_at:
        raise ValueError('committed_at must not be before cycle_at')
    _require_non_empty_string(alarm_configuration_revision, 'alarm_configuration_revision')
    _require_non_empty_string(tool_registry_revision, 'tool_registry_revision')
    _require_non_empty_string(runtime_artifact_version, 'runtime_artifact_version')
    if previous_commit_id is not None:
        _require_non_empty_string(previous_commit_id, 'previous_commit_id')
    evaluation_map = _index_evaluations(evaluations, cycle_at=cycle_at)
    cycle_id = cycle_id_for(cycle_at)
    commit_id = commit_id_for(cycle_id, decision.state.priority_group)
    evidence = materialize_evidence(
        previous_state,
        decision,
        evaluations=evaluation_map,
        evidence_sampling_interval_seconds=evidence_sampling_interval_seconds,
        technical_evidence_contract=technical_evidence_contract,
    )
    receipts = _build_receipts(decision, commit_id=commit_id, committed_at=committed_at)
    journey_events = materialize_journey(
        previous_state,
        decision,
        state=evidence.state,
        cycle_at=cycle_at,
        previous_priority_resolution=previous_priority_resolution,
    )
    runtime_state_updates = _runtime_state_updates(previous_state, evidence.state)
    occurrence_changes = tuple(
        OccurrenceChangeReference(
            occurrence_id=change.occurrence.occurrence_id,
            alarm_identity=change.occurrence.alarm_identity,
            kind=change.kind,
        )
        for change in decision.occurrence_changes
    )
    episode_change = _episode_change_reference(decision.episode_changes)
    management_effect_records = _management_effect_records(previous_state, decision)
    deactivation_request_records = _deactivation_request_records(decision)
    deactivation_effect_records = _deactivation_effect_records(previous_state, decision)
    assignment_change_records = _assignment_change_records(
        decision.assignment_changes,
        previous_state=previous_state,
        next_state=evidence.state,
    )
    affected = _affected_alarms(
        runtime_state_updates=runtime_state_updates,
        decision=decision,
        evidence_records=evidence.records,
        journey_events=journey_events,
        receipts=receipts,
    )
    if not affected:
        return None
    records = EngineCommitRecords(
        occurrence_changes=decision.occurrence_changes,
        episode_changes=decision.episode_changes,
        journey_events=journey_events,
        evidence_records=evidence.records,
        management_effects=management_effect_records,
        deactivation_requests=deactivation_request_records,
        deactivation_effects=deactivation_effect_records,
        assignment_changes=assignment_change_records,
        input_receipts=receipts,
    )
    commit = EngineCommit(
        commit_id=commit_id,
        cycle_id=cycle_id,
        priority_group=evidence.state.priority_group,
        previous_commit_id=previous_commit_id,
        evaluated_at=cycle_at,
        committed_at=committed_at,
        alarm_configuration_revision=alarm_configuration_revision,
        tool_registry_revision=tool_registry_revision,
        runtime_artifact_version=runtime_artifact_version,
        affected_alarms=affected,
        runtime_state_updates=runtime_state_updates,
        occurrence_changes=occurrence_changes,
        episode_change=episode_change,
        journey_event_ids=tuple(event.event_id for event in journey_events),
        evidence_record_ids=tuple(record.evidence_id for record in evidence.records),
        management_effect_ids=tuple(
            sorted({record.effect_id for record in management_effect_records})
        ),
        deactivation_request_ids=tuple(
            record.request_id for record in deactivation_request_records
        ),
        deactivation_effect_ids=tuple(
            sorted({record.effect_id for record in deactivation_effect_records})
        ),
        assignment_change_ids=tuple(record.change_id for record in assignment_change_records),
        receipt_ids=tuple(receipt.receipt_id for receipt in receipts),
    )
    return GroupCommitMaterialization(state=evidence.state, commit=commit, records=records)


# Función _build_receipts: transformación determinística; sus efectos externos se componen fuera del Core.
def _build_receipts(
    decision: GroupLifecycleDecision,
    *,
    commit_id: str,
    committed_at: datetime,
) -> tuple[InputReceipt, ...]:
    deactivation_by_input = {
        result.action.input_id: result for result in decision.deactivation_request_results
    }
    receipts: list[InputReceipt] = []
    for result in decision.management_action_results:
        deactivation = deactivation_by_input.get(result.action.input_id)
        if deactivation is None:
            input_kind = InputKind.MANAGEMENT
            outcome = result.outcome.value
        else:
            input_kind = InputKind.DEACTIVATION_REQUEST
            outcome = deactivation.outcome.value
        receipts.append(
            InputReceipt(
                input_id=result.action.input_id,
                input_kind=input_kind,
                commit_id=commit_id,
                applied_at=committed_at,
                outcome=outcome,
            )
        )
    for result in decision.deactivation_decision_results:
        if result.outcome is DeactivationDecisionOutcome.PENDING_DEPENDENCY:
            continue
        receipts.append(
            InputReceipt(
                input_id=result.decision.decision_id,
                input_kind=InputKind.DEACTIVATION_DECISION,
                commit_id=commit_id,
                applied_at=committed_at,
                outcome=result.outcome.value,
            )
        )
    return tuple(sorted(receipts, key=lambda item: item.receipt_id))


# Función _runtime_state_updates: transformación determinística; sus efectos externos se componen fuera del Core.
def _runtime_state_updates(
    previous_state: GroupLifecycleState,
    next_state: GroupLifecycleState,
) -> tuple[AlarmIdentity, ...]:
    identities = sorted(
        {alarm.alarm_identity for alarm in previous_state.alarms}
        | {alarm.alarm_identity for alarm in next_state.alarms}
    )
    return tuple(
        identity
        for identity in identities
        if previous_state.get(identity) != next_state.get(identity)
    )


# Función _episode_change_reference: transformación determinística; sus efectos externos se componen fuera del Core.
def _episode_change_reference(
    changes: Sequence[EpisodeChange],
) -> EpisodeChangeReference | None:
    if not changes:
        return None
    if len(changes) != 1:
        raise AlarmContractError('one group commit must not contain multiple episode changes')
    change = changes[0]
    return EpisodeChangeReference(
        episode_id=change.episode.episode_id,
        kind=change.kind.value,
    )


# Función _management_effect_records: transformación determinística; sus efectos externos se componen fuera del Core.
def _management_effect_records(
    previous_state: GroupLifecycleState,
    decision: GroupLifecycleDecision,
) -> tuple[ManagementEffectRecord, ...]:
    records: list[ManagementEffectRecord] = []
    started_by_alarm = {
        change.alarm_identity: change.management_effect
        for change in decision.management_effect_changes
        if change.management_effect is not None
    }
    for change in decision.management_effect_changes:
        effect = change.management_effect
        if effect is None:
            previous = previous_state.get(change.alarm_identity)
            if previous is not None:
                effect = previous.management_effect
            if effect is None:
                effect = started_by_alarm.get(change.alarm_identity)
        if effect is None:
            raise AlarmContractError('management effect change requires stable effect identity')
        records.append(
            ManagementEffectRecord(
                record_id=(
                    f'management-effect:{effect.effect_id}:{change.kind.value}:'
                    f'{_timestamp(change.effective_at)}'
                ),
                effect_id=effect.effect_id,
                kind=change.kind,
                alarm_identity=change.alarm_identity,
                effective_at=change.effective_at,
                source_occurrence_id=effect.source_occurrence_id,
                effect_effective_at=effect.effective_at,
                reappearance_due_at=effect.reappearance_due_at,
            )
        )
    return tuple(sorted(records, key=lambda item: (item.effective_at, item.record_id)))


# Sólo los requests creados por este ciclo se registran aquí; una decisión posterior no recrea el request.
def _deactivation_request_records(
    decision: GroupLifecycleDecision,
) -> tuple[DeactivationRequestRecord, ...]:
    records = tuple(
        DeactivationRequestRecord.from_request(result.deactivation_request)
        for result in decision.deactivation_request_results
        if result.deactivation_request is not None
    )
    return tuple(sorted(records, key=lambda item: (item.requested_at, item.request_id)))


# Función _deactivation_effect_records: transformación determinística; sus efectos externos se componen fuera del Core.
def _deactivation_effect_records(
    previous_state: GroupLifecycleState,
    decision: GroupLifecycleDecision,
) -> tuple[DeactivationEffectRecord, ...]:
    records: list[DeactivationEffectRecord] = []
    started_by_alarm = {
        change.alarm_identity: change.deactivation_effect
        for change in decision.deactivation_effect_changes
        if change.deactivation_effect is not None
    }
    for change in decision.deactivation_effect_changes:
        effect = change.deactivation_effect
        if effect is None:
            previous = previous_state.get(change.alarm_identity)
            if previous is not None:
                effect = previous.deactivation_effect
            if effect is None:
                effect = started_by_alarm.get(change.alarm_identity)
        if effect is None:
            raise AlarmContractError('deactivation effect change requires stable effect identity')
        records.append(
            DeactivationEffectRecord(
                record_id=(
                    f'deactivation-effect:{effect.effect_id}:{change.kind.value}:'
                    f'{_timestamp(change.effective_at)}'
                ),
                effect_id=effect.effect_id,
                kind=change.kind,
                alarm_identity=change.alarm_identity,
                effective_at=change.effective_at,
                effective_from=effect.effective_from,
                effective_until=effect.effective_until,
            )
        )
    return tuple(sorted(records, key=lambda item: (item.effective_at, item.record_id)))


# Función _assignment_change_records: transformación determinística; sus efectos externos se componen fuera del Core.
def _assignment_change_records(
    changes: Sequence[AssignmentChange],
    *,
    previous_state: GroupLifecycleState,
    next_state: GroupLifecycleState,
) -> tuple[AssignmentChangeRecord, ...]:
    records: list[AssignmentChangeRecord] = []
    for change in changes:
        occurrence = _occurrence_for_identity(
            change.alarm_identity,
            previous_state=previous_state,
            next_state=next_state,
        )
        if occurrence is None:
            raise AlarmContractError('assignment change requires an occurrence reference')
        boundary = change.due_at if change.due_at is not None else change.effective_at
        change_id = (
            f'assignment:{occurrence.occurrence_id}:{change.tool_key}:'
            f'{change.kind.value}:{_timestamp(boundary)}'
        )
        records.append(
            AssignmentChangeRecord(
                change_id=change_id,
                occurrence_id=occurrence.occurrence_id,
                kind=change.kind,
                alarm_identity=change.alarm_identity,
                tool_key=change.tool_key,
                effective_at=change.effective_at,
                due_at=change.due_at,
            )
        )
    return tuple(sorted(records, key=lambda item: (item.effective_at, item.change_id)))


# Función _affected_alarms: transformación determinística; sus efectos externos se componen fuera del Core.
def _affected_alarms(
    *,
    runtime_state_updates: Sequence[AlarmIdentity],
    decision: GroupLifecycleDecision,
    evidence_records: Sequence[EvidenceRecord],
    journey_events: Sequence[JourneyEvent],
    receipts: Sequence[InputReceipt],
) -> tuple[AlarmIdentity, ...]:
    affected = set(runtime_state_updates)
    affected.update(change.occurrence.alarm_identity for change in decision.occurrence_changes)
    affected.update(change.alarm_identity for change in decision.technical_hold_changes)
    affected.update(result.action.alarm_identity for result in decision.management_action_results)
    affected.update(
        result.action.alarm_identity for result in decision.deactivation_request_results
    )
    for result in decision.deactivation_decision_results:
        if result.deactivation_request is not None:
            affected.add(result.deactivation_request.alarm_identity)
    affected.update(change.alarm_identity for change in decision.management_effect_changes)
    affected.update(change.alarm_identity for change in decision.deactivation_effect_changes)
    affected.update(change.alarm_identity for change in decision.reappearance_changes)
    affected.update(change.alarm_identity for change in decision.assignment_changes)
    affected.update(record.alarm_identity for record in evidence_records)
    affected.update(event.alarm_identity for event in journey_events)
    if receipts and not affected:
        raise AlarmContractError('input receipts require an affected alarm')
    return tuple(sorted(affected))


# Función _index_evaluations: transformación determinística; sus efectos externos se componen fuera del Core.
def _index_evaluations(
    evaluations: Sequence[AlarmEvaluation],
    *,
    cycle_at: datetime,
) -> Mapping[AlarmIdentity, AlarmEvaluation]:
    indexed: dict[AlarmIdentity, AlarmEvaluation] = {}
    for evaluation in evaluations:
        if not isinstance(evaluation, AlarmEvaluation):
            raise TypeError('evaluations must contain AlarmEvaluation values')
        if evaluation.alarm_identity in indexed:
            raise AlarmContractError('evaluations must not contain duplicate identities')
        if evaluation.evaluated_at != cycle_at:
            raise AlarmContractError('evaluations must use the frozen cycle_at')
        indexed[evaluation.alarm_identity] = evaluation
    return MappingProxyType(indexed)


# Función _occurrence_for_identity: transformación determinística; sus efectos externos se componen fuera del Core.
def _occurrence_for_identity(
    identity: AlarmIdentity,
    *,
    previous_state: GroupLifecycleState,
    next_state: GroupLifecycleState,
):
    for state in (next_state, previous_state):
        runtime = state.get(identity)
        if runtime is not None and runtime.occurrence is not None:
            return runtime.occurrence
    return None


# Función _occurrence_change_document: transformación determinística; sus efectos externos se componen fuera del Core.
def _occurrence_change_document(change: OccurrenceChange) -> dict[str, Any]:
    occurrence = change.occurrence
    return {
        'kind': change.kind.value,
        'occurrence_id': occurrence.occurrence_id,
        'alarm_key': occurrence.alarm_identity.canonical_key,
        'episode_id': occurrence.episode_id,
        'started_at': _timestamp(occurrence.started_at),
        'ended_at': None if occurrence.ended_at is None else _timestamp(occurrence.ended_at),
        'closure_reason': (
            None if occurrence.closure_reason is None else occurrence.closure_reason.value
        ),
        'alarm_configuration_revision': occurrence.alarm_configuration_revision,
        'tool_registry_revision': occurrence.tool_registry_revision,
    }


# Función _episode_change_document: transformación determinística; sus efectos externos se componen fuera del Core.
def _episode_change_document(change: EpisodeChange) -> dict[str, Any]:
    episode = change.episode
    return {
        'kind': change.kind.value,
        'episode_id': episode.episode_id,
        'priority_group': episode.priority_group,
        'started_at': _timestamp(episode.started_at),
        'ended_at': None if episode.ended_at is None else _timestamp(episode.ended_at),
        'closure_reason': None if episode.closure_reason is None else episode.closure_reason.value,
    }


# Función _timestamp: transformación determinística; sus efectos externos se componen fuera del Core.
def _timestamp(value: datetime) -> str:
    _require_utc_datetime(value, 'timestamp')
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


# Función _require_non_empty_string: transformación determinística; sus efectos externos se componen fuera del Core.
def _require_non_empty_string(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f'{name} must be a str')
    if not value.strip():
        raise ValueError(f'{name} must not be empty')


# Función _require_utc_datetime: transformación determinística; sus efectos externos se componen fuera del Core.
def _require_utc_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f'{name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f'{name} must be UTC')


# Función _require_typed_tuple: transformación determinística; sus efectos externos se componen fuera del Core.
def _require_typed_tuple(value: object, item_type: type, name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f'{name} must be a tuple')
    if not all(isinstance(item, item_type) for item in value):
        raise TypeError(f'{name} contains an invalid value')


# Función _require_unique_tuple: transformación determinística; sus efectos externos se componen fuera del Core.
def _require_unique_tuple(value: object, item_type: type, name: str) -> None:
    _require_typed_tuple(value, item_type, name)
    if len(set(value)) != len(value):
        raise ValueError(f'{name} must not contain duplicates')


# Función _require_unique_string_tuple: transformación determinística; sus efectos externos se componen fuera del Core.
def _require_unique_string_tuple(value: object, name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f'{name} must be a tuple')
    seen: set[str] = set()
    for item in value:
        _require_non_empty_string(item, name)
        if item in seen:
            raise ValueError(f'{name} must not contain duplicates')
        seen.add(item)


# Función _require_unique_ids: transformación determinística; sus efectos externos se componen fuera del Core.
def _require_unique_ids(values: Sequence[Any], attribute: str, name: str) -> None:
    seen: set[str] = set()
    for value in values:
        identifier = getattr(value, attribute)
        if identifier in seen:
            raise ValueError(f'{name} must not contain duplicate identities')
        seen.add(identifier)
