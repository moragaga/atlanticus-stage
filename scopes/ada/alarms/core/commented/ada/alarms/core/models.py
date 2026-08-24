# Espejo pedagógico de los contratos puros del primer Alarm Core.
# AlarmStatus sólo admite ACTIVE, INACTIVE y ERROR; ERROR nunca afirma estado físico.
# Occurrence representa una activación concreta y Episode correlaciona continuidad dentro del priority_group.
# El hot state conserva sólo memoria necesaria para decisiones futuras; no es historia.
# Una Occurrence abierta exige una última evaluación compacta ACTIVE o ERROR; INACTIVE implica cierre.
# PlannedAlarm es una vista del execution plan y no reemplaza el contrato completo de AlarmDefinition.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from ada.alarms.core.errors import AlarmContractError

TECHNICAL_HOLD_GRACE_SECONDS = 300


class AlarmKind(StrEnum):
    RISK = 'RISK'
    IMPACT = 'IMPACT'


class Criticality(StrEnum):
    C1 = 'C1'
    C2 = 'C2'
    C3 = 'C3'


class AlarmStatus(StrEnum):
    ACTIVE = 'ACTIVE'
    INACTIVE = 'INACTIVE'
    ERROR = 'ERROR'


class EvaluationErrorOrigin(StrEnum):
    QUALITY = 'QUALITY'
    EVALUATOR = 'EVALUATOR'
    RUNTIME = 'RUNTIME'


class OccurrenceClosureReason(StrEnum):
    CONDITION_NORMALIZED = 'condition_normalized'
    TECHNICAL_HOLD_EXPIRED = 'technical_hold_expired'
    CONFIGURATION_DISABLED = 'configuration_disabled'
    CONFIGURATION_REMOVED = 'configuration_removed'
    CONFIGURATION_RECONFIGURED = 'configuration_reconfigured'


class EpisodeClosureReason(StrEnum):
    CONDITION_NORMALIZED = 'condition_normalized'
    TECHNICAL_UNCERTAINTY = 'technical_uncertainty'
    CONFIGURATION_TERMINATED = 'configuration_terminated'


class OccurrenceChangeKind(StrEnum):
    STARTED = 'STARTED'
    CLOSED = 'CLOSED'


class EpisodeChangeKind(StrEnum):
    STARTED = 'STARTED'
    CLOSED = 'CLOSED'


class TechnicalHoldChangeKind(StrEnum):
    STARTED = 'STARTED'
    CLEARED = 'CLEARED'


@dataclass(frozen=True, slots=True, order=True)
class AlarmIdentity:
    family_key: str
    alarm_key: str

    def __post_init__(self) -> None:
        _require_non_empty_string(self.family_key, 'family_key')
        _require_non_empty_string(self.alarm_key, 'alarm_key')

    @property
    def canonical_key(self) -> str:
        return f'{self.family_key}/{self.alarm_key}'


@dataclass(frozen=True, slots=True)
class PlannedAlarm:
    identity: AlarmIdentity
    kind: AlarmKind
    criticality: Criticality
    priority_group: str
    priority_order: int
    delivery_enabled: bool
    evaluator_key: str
    alarm_configuration_revision: str
    tool_registry_revision: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, AlarmIdentity):
            raise TypeError('identity must be an AlarmIdentity')
        if not isinstance(self.kind, AlarmKind):
            raise TypeError('kind must be an AlarmKind')
        if not isinstance(self.criticality, Criticality):
            raise TypeError('criticality must be a Criticality')
        _require_non_empty_string(self.priority_group, 'priority_group')
        if isinstance(self.priority_order, bool) or not isinstance(self.priority_order, int):
            raise TypeError('priority_order must be an int')
        if self.priority_order <= 0:
            raise ValueError('priority_order must be greater than zero')
        if not isinstance(self.delivery_enabled, bool):
            raise TypeError('delivery_enabled must be a bool')
        _require_non_empty_string(self.evaluator_key, 'evaluator_key')
        _require_non_empty_string(
            self.alarm_configuration_revision,
            'alarm_configuration_revision',
        )
        _require_non_empty_string(self.tool_registry_revision, 'tool_registry_revision')


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    alarm_identity: AlarmIdentity
    now: datetime
    parameters: Mapping[str, str | float | bool]
    data: Any

    def __post_init__(self) -> None:
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        _require_utc_datetime(self.now, 'now')
        if not isinstance(self.parameters, Mapping):
            raise TypeError('parameters must be a mapping')
        normalized: dict[str, str | float | bool] = {}
        for key, value in self.parameters.items():
            _require_non_empty_string(key, 'parameter key')
            if isinstance(value, (bool, str, float)):
                normalized[key] = value
            else:
                raise TypeError('parameter values must be TEXT, FLOAT, or BOOLEAN')
        object.__setattr__(self, 'parameters', MappingProxyType(normalized))


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    contract_key: str
    contract_version: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_non_empty_string(self.contract_key, 'contract_key')
        _require_non_empty_string(self.contract_version, 'contract_version')
        if not isinstance(self.payload, Mapping):
            raise TypeError('payload must be a mapping')
        object.__setattr__(self, 'payload', MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class AffectedInputIssue:
    reason_key: str
    source_key: str | None = None
    scope_key: str | None = None
    resource_key: str | None = None
    fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty_string(self.reason_key, 'reason_key')
        for name in ('source_key', 'scope_key', 'resource_key'):
            value = getattr(self, name)
            if value is not None:
                _require_non_empty_string(value, name)
        if not isinstance(self.fields, tuple):
            raise TypeError('fields must be a tuple')
        seen: set[str] = set()
        for field in self.fields:
            _require_non_empty_string(field, 'field')
            if field in seen:
                raise ValueError('fields must not contain duplicates')
            seen.add(field)


@dataclass(frozen=True, slots=True)
class EvaluationError:
    origin: EvaluationErrorOrigin
    error_key: str
    message: str
    affected_inputs: tuple[AffectedInputIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.origin, EvaluationErrorOrigin):
            raise TypeError('origin must be an EvaluationErrorOrigin')
        _require_non_empty_string(self.error_key, 'error_key')
        _require_non_empty_string(self.message, 'message')
        if not isinstance(self.affected_inputs, tuple):
            raise TypeError('affected_inputs must be a tuple')
        for issue in self.affected_inputs:
            if not isinstance(issue, AffectedInputIssue):
                raise TypeError('affected_inputs must contain AffectedInputIssue values')


@dataclass(frozen=True, slots=True)
class AlarmEvaluation:
    alarm_identity: AlarmIdentity
    status: AlarmStatus
    evaluated_at: datetime
    evidence_snapshot: EvidenceSnapshot | None = None
    error: EvaluationError | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        if not isinstance(self.status, AlarmStatus):
            raise TypeError('status must be an AlarmStatus')
        _require_utc_datetime(self.evaluated_at, 'evaluated_at')
        if self.status is AlarmStatus.ERROR:
            if self.error is None:
                raise ValueError('ERROR evaluation requires error')
            if not isinstance(self.error, EvaluationError):
                raise TypeError('error must be an EvaluationError')
            if self.evidence_snapshot is not None:
                raise ValueError('ERROR evaluation must not contain physical evidence_snapshot')
            return
        if self.evidence_snapshot is None:
            raise ValueError('ACTIVE and INACTIVE evaluations require evidence_snapshot')
        if not isinstance(self.evidence_snapshot, EvidenceSnapshot):
            raise TypeError('evidence_snapshot must be an EvidenceSnapshot')
        if self.error is not None:
            raise ValueError('ACTIVE and INACTIVE evaluations must not contain error')


@dataclass(frozen=True, slots=True)
class AlarmOccurrence:
    occurrence_id: str
    alarm_identity: AlarmIdentity
    episode_id: str
    started_at: datetime
    alarm_configuration_revision: str
    tool_registry_revision: str
    ended_at: datetime | None = None
    closure_reason: OccurrenceClosureReason | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.occurrence_id, 'occurrence_id')
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        _require_non_empty_string(self.episode_id, 'episode_id')
        _require_utc_datetime(self.started_at, 'started_at')
        _require_non_empty_string(
            self.alarm_configuration_revision,
            'alarm_configuration_revision',
        )
        _require_non_empty_string(self.tool_registry_revision, 'tool_registry_revision')
        if self.ended_at is None:
            if self.closure_reason is not None:
                raise ValueError('open occurrence must not have closure_reason')
            return
        _require_utc_datetime(self.ended_at, 'ended_at')
        if self.ended_at < self.started_at:
            raise ValueError('ended_at must not be before started_at')
        if not isinstance(self.closure_reason, OccurrenceClosureReason):
            raise ValueError('closed occurrence requires closure_reason')

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    def close(
        self,
        *,
        ended_at: datetime,
        reason: OccurrenceClosureReason,
    ) -> AlarmOccurrence:
        if not self.is_open:
            raise AlarmContractError('closed occurrence is immutable')
        return AlarmOccurrence(
            occurrence_id=self.occurrence_id,
            alarm_identity=self.alarm_identity,
            episode_id=self.episode_id,
            started_at=self.started_at,
            alarm_configuration_revision=self.alarm_configuration_revision,
            tool_registry_revision=self.tool_registry_revision,
            ended_at=ended_at,
            closure_reason=reason,
        )


@dataclass(frozen=True, slots=True)
class AlarmEpisode:
    episode_id: str
    priority_group: str
    started_at: datetime
    ended_at: datetime | None = None
    closure_reason: EpisodeClosureReason | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.episode_id, 'episode_id')
        _require_non_empty_string(self.priority_group, 'priority_group')
        _require_utc_datetime(self.started_at, 'started_at')
        if self.ended_at is None:
            if self.closure_reason is not None:
                raise ValueError('open episode must not have closure_reason')
            return
        _require_utc_datetime(self.ended_at, 'ended_at')
        if self.ended_at < self.started_at:
            raise ValueError('ended_at must not be before started_at')
        if not isinstance(self.closure_reason, EpisodeClosureReason):
            raise ValueError('closed episode requires closure_reason')

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    def close(self, *, ended_at: datetime, reason: EpisodeClosureReason) -> AlarmEpisode:
        if not self.is_open:
            raise AlarmContractError('closed episode is immutable')
        return AlarmEpisode(
            episode_id=self.episode_id,
            priority_group=self.priority_group,
            started_at=self.started_at,
            ended_at=ended_at,
            closure_reason=reason,
        )


@dataclass(frozen=True, slots=True)
class TechnicalHold:
    started_at: datetime
    due_at: datetime

    def __post_init__(self) -> None:
        _require_utc_datetime(self.started_at, 'started_at')
        _require_utc_datetime(self.due_at, 'due_at')
        if self.due_at <= self.started_at:
            raise ValueError('due_at must be after started_at')


@dataclass(frozen=True, slots=True)
class AlarmRuntimeState:
    alarm_identity: AlarmIdentity
    occurrence: AlarmOccurrence | None = None
    last_evaluation: AlarmEvaluation | None = None
    technical_hold: TechnicalHold | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        if self.occurrence is not None:
            if not isinstance(self.occurrence, AlarmOccurrence):
                raise TypeError('occurrence must be an AlarmOccurrence')
            if self.occurrence.alarm_identity != self.alarm_identity:
                raise ValueError('occurrence identity must match runtime state')
            if not self.occurrence.is_open:
                raise ValueError('runtime state must not retain a closed occurrence')
        if self.last_evaluation is not None:
            if not isinstance(self.last_evaluation, AlarmEvaluation):
                raise TypeError('last_evaluation must be an AlarmEvaluation')
            if self.last_evaluation.alarm_identity != self.alarm_identity:
                raise ValueError('last_evaluation identity must match runtime state')
        if self.occurrence is not None:
            if self.last_evaluation is None:
                raise ValueError('open occurrence requires last_evaluation')
            if self.last_evaluation.status is AlarmStatus.INACTIVE:
                raise ValueError('open occurrence must not retain last_evaluation INACTIVE')
        elif self.last_evaluation is not None:
            raise ValueError('last_evaluation requires an open occurrence')
        if self.technical_hold is not None:
            if not isinstance(self.technical_hold, TechnicalHold):
                raise TypeError('technical_hold must be a TechnicalHold')
            if self.occurrence is None:
                raise ValueError('technical_hold requires an open occurrence')
            if self.last_evaluation is None or self.last_evaluation.status is not AlarmStatus.ERROR:
                raise ValueError('technical_hold requires last_evaluation ERROR')


@dataclass(frozen=True, slots=True)
class GroupLifecycleState:
    priority_group: str
    episode: AlarmEpisode | None = None
    alarms: tuple[AlarmRuntimeState, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty_string(self.priority_group, 'priority_group')
        if self.episode is not None:
            if not isinstance(self.episode, AlarmEpisode):
                raise TypeError('episode must be an AlarmEpisode')
            if self.episode.priority_group != self.priority_group:
                raise ValueError('episode priority_group must match group state')
            if not self.episode.is_open:
                raise ValueError('group state must not retain a closed episode')
        if not isinstance(self.alarms, tuple):
            raise TypeError('alarms must be a tuple')
        seen: set[AlarmIdentity] = set()
        for alarm in self.alarms:
            if not isinstance(alarm, AlarmRuntimeState):
                raise TypeError('alarms must contain AlarmRuntimeState values')
            if alarm.alarm_identity in seen:
                raise ValueError('alarms must not contain duplicate identities')
            seen.add(alarm.alarm_identity)
            if alarm.occurrence is not None:
                if self.episode is None:
                    raise ValueError('open occurrence requires an open episode')
                if alarm.occurrence.episode_id != self.episode.episode_id:
                    raise ValueError('occurrence episode_id must match group episode')
        if self.episode is not None and not any(
            alarm.occurrence is not None for alarm in self.alarms
        ):
            raise ValueError('open episode requires at least one open occurrence')

    def get(self, identity: AlarmIdentity) -> AlarmRuntimeState | None:
        for alarm in self.alarms:
            if alarm.alarm_identity == identity:
                return alarm
        return None


@dataclass(frozen=True, slots=True)
class ConfigurationClosure:
    alarm_identity: AlarmIdentity
    reason: OccurrenceClosureReason
    effective_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        if not isinstance(self.reason, OccurrenceClosureReason):
            raise TypeError('reason must be an OccurrenceClosureReason')
        if self.reason not in {
            OccurrenceClosureReason.CONFIGURATION_DISABLED,
            OccurrenceClosureReason.CONFIGURATION_REMOVED,
        }:
            raise ValueError('configuration closure reason is invalid')
        _require_utc_datetime(self.effective_at, 'effective_at')


@dataclass(frozen=True, slots=True)
class OccurrenceChange:
    kind: OccurrenceChangeKind
    occurrence: AlarmOccurrence

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OccurrenceChangeKind):
            raise TypeError('kind must be an OccurrenceChangeKind')
        if not isinstance(self.occurrence, AlarmOccurrence):
            raise TypeError('occurrence must be an AlarmOccurrence')
        if self.kind is OccurrenceChangeKind.STARTED and not self.occurrence.is_open:
            raise ValueError('STARTED occurrence change requires open occurrence')
        if self.kind is OccurrenceChangeKind.CLOSED and self.occurrence.is_open:
            raise ValueError('CLOSED occurrence change requires closed occurrence')


@dataclass(frozen=True, slots=True)
class EpisodeChange:
    kind: EpisodeChangeKind
    episode: AlarmEpisode

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EpisodeChangeKind):
            raise TypeError('kind must be an EpisodeChangeKind')
        if not isinstance(self.episode, AlarmEpisode):
            raise TypeError('episode must be an AlarmEpisode')
        if self.kind is EpisodeChangeKind.STARTED and not self.episode.is_open:
            raise ValueError('STARTED episode change requires open episode')
        if self.kind is EpisodeChangeKind.CLOSED and self.episode.is_open:
            raise ValueError('CLOSED episode change requires closed episode')


@dataclass(frozen=True, slots=True)
class TechnicalHoldChange:
    kind: TechnicalHoldChangeKind
    alarm_identity: AlarmIdentity
    occurrence_id: str
    effective_at: datetime
    technical_hold: TechnicalHold | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TechnicalHoldChangeKind):
            raise TypeError('kind must be a TechnicalHoldChangeKind')
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        _require_non_empty_string(self.occurrence_id, 'occurrence_id')
        _require_utc_datetime(self.effective_at, 'effective_at')
        if self.kind is TechnicalHoldChangeKind.STARTED:
            if not isinstance(self.technical_hold, TechnicalHold):
                raise ValueError('STARTED technical hold change requires technical_hold')
            if self.effective_at != self.technical_hold.started_at:
                raise ValueError('STARTED technical hold effective_at must match started_at')
        elif self.technical_hold is not None:
            raise ValueError('CLEARED technical hold change must not contain technical_hold')


@dataclass(frozen=True, slots=True)
class GroupLifecycleDecision:
    state: GroupLifecycleState
    occurrence_changes: tuple[OccurrenceChange, ...] = ()
    episode_changes: tuple[EpisodeChange, ...] = ()
    technical_hold_changes: tuple[TechnicalHoldChange, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.state, GroupLifecycleState):
            raise TypeError('state must be a GroupLifecycleState')
        if not isinstance(self.occurrence_changes, tuple):
            raise TypeError('occurrence_changes must be a tuple')
        if not isinstance(self.episode_changes, tuple):
            raise TypeError('episode_changes must be a tuple')
        if not isinstance(self.technical_hold_changes, tuple):
            raise TypeError('technical_hold_changes must be a tuple')
        for change in self.occurrence_changes:
            if not isinstance(change, OccurrenceChange):
                raise TypeError('occurrence_changes must contain OccurrenceChange values')
        for change in self.episode_changes:
            if not isinstance(change, EpisodeChange):
                raise TypeError('episode_changes must contain EpisodeChange values')
        for change in self.technical_hold_changes:
            if not isinstance(change, TechnicalHoldChange):
                raise TypeError('technical_hold_changes must contain TechnicalHoldChange values')

    @property
    def has_lifecycle_change(self) -> bool:
        return bool(self.occurrence_changes or self.episode_changes or self.technical_hold_changes)


def _require_non_empty_string(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f'{name} must be a string')
    if not value.strip():
        raise ValueError(f'{name} must not be empty')


def _require_utc_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f'{name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f'{name} must be timezone-aware UTC')
