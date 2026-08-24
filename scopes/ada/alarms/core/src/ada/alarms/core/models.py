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


class PriorityDisposition(StrEnum):
    PREDOMINANT = 'PREDOMINANT'
    ECLIPSED = 'ECLIPSED'
    CASCADE_SUPPRESSED = 'CASCADE_SUPPRESSED'
    DEACTIVATED = 'DEACTIVATED'
    SHADOW = 'SHADOW'


class AssignmentChangeKind(StrEnum):
    ASSIGNED = 'ASSIGNED'
    REMOVED = 'REMOVED'
    SCHEDULED = 'SCHEDULED'
    RESCHEDULED = 'RESCHEDULED'


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


class ManagementActionOutcome(StrEnum):
    EFFECTIVE = 'EFFECTIVE'
    ADDITIONAL = 'ADDITIONAL'
    LATE = 'LATE'


class ManagementEffectChangeKind(StrEnum):
    STARTED = 'STARTED'
    CLEARED = 'CLEARED'


class DeactivationRequestOutcome(StrEnum):
    DIRECT = 'DIRECT'
    PENDING_APPROVAL = 'PENDING_APPROVAL'
    ADDITIONAL = 'ADDITIONAL'
    UNAVAILABLE = 'UNAVAILABLE'
    LATE = 'LATE'


class DeactivationDecisionKind(StrEnum):
    APPROVED = 'APPROVED'
    REJECTED = 'REJECTED'
    CANCELLED = 'CANCELLED'
    INVALIDATED = 'INVALIDATED'


class DeactivationDecisionOutcome(StrEnum):
    APPLIED = 'APPLIED'
    REJECTED = 'REJECTED'
    CANCELLED = 'CANCELLED'
    INVALIDATED = 'INVALIDATED'
    EXPIRED = 'EXPIRED'
    STALE_TARGET = 'STALE_TARGET'
    PENDING_DEPENDENCY = 'PENDING_DEPENDENCY'


class DeactivationEffectChangeKind(StrEnum):
    STARTED = 'STARTED'
    CLEARED = 'CLEARED'


@dataclass(frozen=True, slots=True, order=True)
class RoutingDestination:
    tool_key: str
    delay_seconds: int | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.tool_key, 'tool_key')
        if self.delay_seconds is None:
            return
        if isinstance(self.delay_seconds, bool) or not isinstance(self.delay_seconds, int):
            raise TypeError('delay_seconds must be an int')
        if self.delay_seconds < 0:
            raise ValueError('delay_seconds must not be negative')


@dataclass(frozen=True, slots=True)
class AlarmRouting:
    origin_tool_key: str
    destinations: tuple[RoutingDestination, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty_string(self.origin_tool_key, 'origin_tool_key')
        if not isinstance(self.destinations, tuple):
            raise TypeError('destinations must be a tuple')
        seen = {self.origin_tool_key}
        normalized: list[RoutingDestination] = []
        for destination in self.destinations:
            if not isinstance(destination, RoutingDestination):
                raise TypeError('destinations must contain RoutingDestination values')
            if destination.tool_key in seen:
                raise ValueError('routing tools must not contain duplicates')
            seen.add(destination.tool_key)
            normalized.append(destination)
        object.__setattr__(self, 'destinations', tuple(sorted(normalized)))


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
class DeactivationPolicy:
    approval_required: bool

    def __post_init__(self) -> None:
        if not isinstance(self.approval_required, bool):
            raise TypeError('approval_required must be a bool')


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
    routing: AlarmRouting
    deactivation_policy: DeactivationPolicy | None = None

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
        if not isinstance(self.routing, AlarmRouting):
            raise TypeError('routing must be an AlarmRouting')
        if self.deactivation_policy is not None and not isinstance(
            self.deactivation_policy, DeactivationPolicy
        ):
            raise TypeError('deactivation_policy must be a DeactivationPolicy')
        if self.criticality is Criticality.C1 and any(
            destination.delay_seconds is not None for destination in self.routing.destinations
        ):
            raise ValueError('C1 routing destinations must be immediate')
        if self.criticality is Criticality.C2 and any(
            destination.delay_seconds is None for destination in self.routing.destinations
        ):
            raise ValueError('C2 routing destinations require delay_seconds')
        if self.criticality is Criticality.C3 and self.routing.destinations:
            raise ValueError('C3 routing must contain only the origin Tool')


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
class RuntimeEvaluationState:
    status: AlarmStatus
    evaluated_at: datetime
    error_key: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, AlarmStatus):
            raise TypeError('status must be an AlarmStatus')
        _require_utc_datetime(self.evaluated_at, 'evaluated_at')
        if self.status is AlarmStatus.ERROR:
            _require_non_empty_string(self.error_key, 'error_key')
        elif self.error_key is not None:
            raise ValueError('ACTIVE and INACTIVE runtime evaluation must not contain error_key')

    @classmethod
    def from_evaluation(cls, evaluation: AlarmEvaluation) -> RuntimeEvaluationState:
        if not isinstance(evaluation, AlarmEvaluation):
            raise TypeError('evaluation must be an AlarmEvaluation')
        return cls(
            status=evaluation.status,
            evaluated_at=evaluation.evaluated_at,
            error_key=(
                evaluation.error.error_key
                if evaluation.status is AlarmStatus.ERROR and evaluation.error is not None
                else None
            ),
        )


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
class DeactivationIntent:
    effective_until: datetime

    def __post_init__(self) -> None:
        _require_utc_datetime(self.effective_until, 'effective_until')


@dataclass(frozen=True, slots=True)
class ManagementAction:
    input_id: str
    alarm_identity: AlarmIdentity
    source_occurrence_id: str | None
    tool_key: str
    actor_key: str
    source_created_at: datetime
    context: Mapping[str, str] = MappingProxyType({})
    deactivation_intent: DeactivationIntent | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.input_id, 'input_id')
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        if self.source_occurrence_id is not None:
            _require_non_empty_string(self.source_occurrence_id, 'source_occurrence_id')
        _require_non_empty_string(self.tool_key, 'tool_key')
        _require_non_empty_string(self.actor_key, 'actor_key')
        _require_utc_datetime(self.source_created_at, 'source_created_at')
        if self.deactivation_intent is not None:
            if not isinstance(self.deactivation_intent, DeactivationIntent):
                raise TypeError('deactivation_intent must be a DeactivationIntent')
            if self.deactivation_intent.effective_until <= self.source_created_at:
                raise ValueError('deactivation effective_until must be after source_created_at')
        if not isinstance(self.context, Mapping):
            raise TypeError('context must be a mapping')
        normalized: dict[str, str] = {}
        for key, value in self.context.items():
            _require_non_empty_string(key, 'context key')
            _require_non_empty_string(value, 'context value')
            normalized[key] = value
        object.__setattr__(self, 'context', MappingProxyType(normalized))


@dataclass(frozen=True, slots=True)
class ManagementEffect:
    effect_id: str
    source_occurrence_id: str
    effective_at: datetime
    reappearance_due_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty_string(self.effect_id, 'effect_id')
        _require_non_empty_string(self.source_occurrence_id, 'source_occurrence_id')
        _require_utc_datetime(self.effective_at, 'effective_at')
        _require_utc_datetime(self.reappearance_due_at, 'reappearance_due_at')
        if self.reappearance_due_at <= self.effective_at:
            raise ValueError('reappearance_due_at must be after effective_at')


@dataclass(frozen=True, slots=True)
class DeactivationRequest:
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


@dataclass(frozen=True, slots=True)
class DeactivationDecision:
    decision_id: str
    request_id: str
    kind: DeactivationDecisionKind
    decided_at: datetime
    actor_key: str

    def __post_init__(self) -> None:
        _require_non_empty_string(self.decision_id, 'decision_id')
        _require_non_empty_string(self.request_id, 'request_id')
        if not isinstance(self.kind, DeactivationDecisionKind):
            raise TypeError('kind must be a DeactivationDecisionKind')
        _require_utc_datetime(self.decided_at, 'decided_at')
        _require_non_empty_string(self.actor_key, 'actor_key')


@dataclass(frozen=True, slots=True)
class DeactivationEffect:
    effect_id: str
    effective_from: datetime
    effective_until: datetime

    def __post_init__(self) -> None:
        _require_non_empty_string(self.effect_id, 'effect_id')
        _require_utc_datetime(self.effective_from, 'effective_from')
        _require_utc_datetime(self.effective_until, 'effective_until')
        if self.effective_until <= self.effective_from:
            raise ValueError('effective_until must be after effective_from')


@dataclass(frozen=True, slots=True, order=True)
class ToolAssignment:
    tool_key: str
    assigned_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty_string(self.tool_key, 'tool_key')
        _require_utc_datetime(self.assigned_at, 'assigned_at')


@dataclass(frozen=True, slots=True, order=True)
class PendingToolAssignment:
    tool_key: str
    due_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty_string(self.tool_key, 'tool_key')
        _require_utc_datetime(self.due_at, 'due_at')


@dataclass(frozen=True, slots=True)
class AlarmRuntimeState:
    alarm_identity: AlarmIdentity
    occurrence: AlarmOccurrence | None = None
    last_evaluation: RuntimeEvaluationState | None = None
    technical_hold: TechnicalHold | None = None
    management_cycle: int | None = None
    management_effect: ManagementEffect | None = None
    deactivation_effect: DeactivationEffect | None = None
    assignments: tuple[ToolAssignment, ...] = ()
    pending_assignments: tuple[PendingToolAssignment, ...] = ()
    next_evidence_due_at: datetime | None = None

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
        if self.last_evaluation is not None and not isinstance(
            self.last_evaluation, RuntimeEvaluationState
        ):
            raise TypeError('last_evaluation must be a RuntimeEvaluationState')
        if self.occurrence is not None:
            if self.last_evaluation is None:
                raise ValueError('open occurrence requires last_evaluation')
            if self.last_evaluation.status is AlarmStatus.INACTIVE:
                raise ValueError('open occurrence must not retain last_evaluation INACTIVE')
            if isinstance(self.management_cycle, bool) or not isinstance(
                self.management_cycle, int
            ):
                raise TypeError('open occurrence requires management_cycle int')
            if self.management_cycle <= 0:
                raise ValueError('management_cycle must be greater than zero')
        else:
            if self.last_evaluation is not None:
                raise ValueError('last_evaluation requires an open occurrence')
            if self.management_cycle is not None:
                raise ValueError('management_cycle requires an open occurrence')
        if self.technical_hold is not None:
            if not isinstance(self.technical_hold, TechnicalHold):
                raise TypeError('technical_hold must be a TechnicalHold')
            if self.occurrence is None:
                raise ValueError('technical_hold requires an open occurrence')
            if self.last_evaluation is None or self.last_evaluation.status is not AlarmStatus.ERROR:
                raise ValueError('technical_hold requires last_evaluation ERROR')
        if self.management_effect is not None:
            if not isinstance(self.management_effect, ManagementEffect):
                raise TypeError('management_effect must be a ManagementEffect')
            if self.occurrence is None and self.technical_hold is not None:
                raise ValueError('management-only state must not retain technical_hold')
        if self.deactivation_effect is not None and not isinstance(
            self.deactivation_effect, DeactivationEffect
        ):
            raise TypeError('deactivation_effect must be a DeactivationEffect')
        if not isinstance(self.assignments, tuple):
            raise TypeError('assignments must be a tuple')
        if not isinstance(self.pending_assignments, tuple):
            raise TypeError('pending_assignments must be a tuple')
        assigned_tools: set[str] = set()
        normalized_assignments: list[ToolAssignment] = []
        for assignment in self.assignments:
            if not isinstance(assignment, ToolAssignment):
                raise TypeError('assignments must contain ToolAssignment values')
            if assignment.tool_key in assigned_tools:
                raise ValueError('assignments must not contain duplicate tool_key values')
            assigned_tools.add(assignment.tool_key)
            normalized_assignments.append(assignment)
        pending_tools: set[str] = set()
        normalized_pending: list[PendingToolAssignment] = []
        for pending in self.pending_assignments:
            if not isinstance(pending, PendingToolAssignment):
                raise TypeError('pending_assignments must contain PendingToolAssignment values')
            if pending.tool_key in pending_tools:
                raise ValueError('pending_assignments must not contain duplicate tool_key values')
            if pending.tool_key in assigned_tools:
                raise ValueError('a Tool must not be both assigned and pending')
            pending_tools.add(pending.tool_key)
            normalized_pending.append(pending)
        if self.occurrence is None and (normalized_assignments or normalized_pending):
            raise ValueError('assignments require an open occurrence')
        if self.next_evidence_due_at is not None:
            _require_utc_datetime(self.next_evidence_due_at, 'next_evidence_due_at')
            if self.occurrence is None:
                raise ValueError('next_evidence_due_at requires an open occurrence')
            if self.technical_hold is not None:
                raise ValueError('technical_hold suspends next_evidence_due_at')
        object.__setattr__(self, 'assignments', tuple(sorted(normalized_assignments)))
        object.__setattr__(self, 'pending_assignments', tuple(sorted(normalized_pending)))


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
            if alarm.management_effect is not None and self.episode is None:
                raise ValueError('management_effect requires an open episode')
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
class ManagementActionResult:
    action: ManagementAction
    outcome: ManagementActionOutcome
    management_cycle: int | None = None
    management_effect_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, ManagementAction):
            raise TypeError('action must be a ManagementAction')
        if not isinstance(self.outcome, ManagementActionOutcome):
            raise TypeError('outcome must be a ManagementActionOutcome')
        if self.management_cycle is not None:
            if isinstance(self.management_cycle, bool) or not isinstance(
                self.management_cycle, int
            ):
                raise TypeError('management_cycle must be an int')
            if self.management_cycle <= 0:
                raise ValueError('management_cycle must be greater than zero')
        if self.management_effect_id is not None:
            _require_non_empty_string(self.management_effect_id, 'management_effect_id')
        if (
            self.outcome is ManagementActionOutcome.ADDITIONAL
            and self.management_effect_id is not None
        ):
            raise ValueError('ADDITIONAL result must not create management_effect_id')


@dataclass(frozen=True, slots=True)
class ManagementEffectChange:
    kind: ManagementEffectChangeKind
    alarm_identity: AlarmIdentity
    effective_at: datetime
    management_effect: ManagementEffect | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ManagementEffectChangeKind):
            raise TypeError('kind must be a ManagementEffectChangeKind')
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        _require_utc_datetime(self.effective_at, 'effective_at')
        if self.kind is ManagementEffectChangeKind.STARTED:
            if not isinstance(self.management_effect, ManagementEffect):
                raise ValueError('STARTED management effect change requires management_effect')
            if self.effective_at != self.management_effect.effective_at:
                raise ValueError('STARTED management effect effective_at must match effect')
        elif self.management_effect is not None:
            raise ValueError('CLEARED management effect change must not contain management_effect')


@dataclass(frozen=True, slots=True)
class DeactivationRequestResult:
    action: ManagementAction
    outcome: DeactivationRequestOutcome
    deactivation_request: DeactivationRequest | None = None
    deactivation_effect_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, ManagementAction):
            raise TypeError('action must be a ManagementAction')
        if not isinstance(self.outcome, DeactivationRequestOutcome):
            raise TypeError('outcome must be a DeactivationRequestOutcome')
        if self.action.deactivation_intent is None:
            raise ValueError('deactivation request result requires deactivation_intent')
        if self.deactivation_request is not None and not isinstance(
            self.deactivation_request, DeactivationRequest
        ):
            raise TypeError('deactivation_request must be a DeactivationRequest')
        if self.deactivation_effect_id is not None:
            _require_non_empty_string(self.deactivation_effect_id, 'deactivation_effect_id')
        if self.outcome is DeactivationRequestOutcome.DIRECT:
            if self.deactivation_request is None or self.deactivation_effect_id is None:
                raise ValueError('DIRECT result requires request and deactivation_effect_id')
        elif self.outcome is DeactivationRequestOutcome.PENDING_APPROVAL:
            if self.deactivation_request is None or self.deactivation_effect_id is not None:
                raise ValueError('PENDING_APPROVAL result requires request without effect')
        elif self.deactivation_request is not None or self.deactivation_effect_id is not None:
            raise ValueError('non-effective deactivation request result must not contain request/effect')


@dataclass(frozen=True, slots=True)
class DeactivationDecisionResult:
    decision: DeactivationDecision
    outcome: DeactivationDecisionOutcome
    deactivation_request: DeactivationRequest | None = None
    deactivation_effect_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, DeactivationDecision):
            raise TypeError('decision must be a DeactivationDecision')
        if not isinstance(self.outcome, DeactivationDecisionOutcome):
            raise TypeError('outcome must be a DeactivationDecisionOutcome')
        if self.deactivation_request is not None and not isinstance(
            self.deactivation_request, DeactivationRequest
        ):
            raise TypeError('deactivation_request must be a DeactivationRequest')
        if self.deactivation_effect_id is not None:
            _require_non_empty_string(self.deactivation_effect_id, 'deactivation_effect_id')
        if self.outcome is DeactivationDecisionOutcome.APPLIED:
            if self.deactivation_request is None or self.deactivation_effect_id is None:
                raise ValueError('APPLIED result requires request and deactivation_effect_id')
        elif self.deactivation_effect_id is not None:
            raise ValueError('non-applied deactivation decision must not contain effect id')


@dataclass(frozen=True, slots=True)
class DeactivationEffectChange:
    kind: DeactivationEffectChangeKind
    alarm_identity: AlarmIdentity
    effective_at: datetime
    deactivation_effect: DeactivationEffect | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DeactivationEffectChangeKind):
            raise TypeError('kind must be a DeactivationEffectChangeKind')
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        _require_utc_datetime(self.effective_at, 'effective_at')
        if self.kind is DeactivationEffectChangeKind.STARTED:
            if not isinstance(self.deactivation_effect, DeactivationEffect):
                raise ValueError('STARTED deactivation effect change requires deactivation_effect')
            if self.effective_at != self.deactivation_effect.effective_from:
                raise ValueError('STARTED deactivation effect effective_at must match effect')
        elif self.deactivation_effect is not None:
            raise ValueError('CLEARED deactivation effect change must not contain deactivation_effect')


@dataclass(frozen=True, slots=True)
class ReappearanceChange:
    alarm_identity: AlarmIdentity
    occurrence_id: str
    effective_at: datetime
    management_cycle: int

    def __post_init__(self) -> None:
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        _require_non_empty_string(self.occurrence_id, 'occurrence_id')
        _require_utc_datetime(self.effective_at, 'effective_at')
        if isinstance(self.management_cycle, bool) or not isinstance(self.management_cycle, int):
            raise TypeError('management_cycle must be an int')
        if self.management_cycle <= 1:
            raise ValueError('reappearance management_cycle must be greater than one')


@dataclass(frozen=True, slots=True)
class CascadeSuppression:
    source_alarm_identity: AlarmIdentity
    source_occurrence_id: str
    management_effect_id: str
    target_alarm_identity: AlarmIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.source_alarm_identity, AlarmIdentity):
            raise TypeError('source_alarm_identity must be an AlarmIdentity')
        _require_non_empty_string(self.source_occurrence_id, 'source_occurrence_id')
        _require_non_empty_string(self.management_effect_id, 'management_effect_id')
        if not isinstance(self.target_alarm_identity, AlarmIdentity):
            raise TypeError('target_alarm_identity must be an AlarmIdentity')
        if self.source_alarm_identity == self.target_alarm_identity:
            raise ValueError('cascade source and target must be different alarms')


@dataclass(frozen=True, slots=True)
class AssignmentChange:
    kind: AssignmentChangeKind
    alarm_identity: AlarmIdentity
    tool_key: str
    effective_at: datetime
    due_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AssignmentChangeKind):
            raise TypeError('kind must be an AssignmentChangeKind')
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        _require_non_empty_string(self.tool_key, 'tool_key')
        _require_utc_datetime(self.effective_at, 'effective_at')
        if self.kind in {AssignmentChangeKind.SCHEDULED, AssignmentChangeKind.RESCHEDULED}:
            if self.due_at is None:
                raise ValueError('pending assignment change requires due_at')
            _require_utc_datetime(self.due_at, 'due_at')
            if self.due_at <= self.effective_at:
                raise ValueError('pending assignment due_at must be after effective_at')
        elif self.due_at is not None:
            raise ValueError('ASSIGNED and REMOVED changes must not contain due_at')


@dataclass(frozen=True, slots=True)
class AlarmPriorityDecision:
    alarm_identity: AlarmIdentity
    disposition: PriorityDisposition
    blocking_alarm_identities: tuple[AlarmIdentity, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        if not isinstance(self.disposition, PriorityDisposition):
            raise TypeError('disposition must be a PriorityDisposition')
        if not isinstance(self.blocking_alarm_identities, tuple):
            raise TypeError('blocking_alarm_identities must be a tuple')
        normalized: list[AlarmIdentity] = []
        seen: set[AlarmIdentity] = set()
        for identity in self.blocking_alarm_identities:
            if not isinstance(identity, AlarmIdentity):
                raise TypeError('blocking_alarm_identities must contain AlarmIdentity values')
            if identity == self.alarm_identity:
                raise ValueError('priority blocker must be a different alarm')
            if identity in seen:
                raise ValueError('blocking_alarm_identities must not contain duplicates')
            seen.add(identity)
            normalized.append(identity)
        object.__setattr__(self, 'blocking_alarm_identities', tuple(sorted(normalized)))
        if self.disposition in {
            PriorityDisposition.PREDOMINANT,
            PriorityDisposition.DEACTIVATED,
            PriorityDisposition.SHADOW,
        }:
            if self.blocking_alarm_identities:
                raise ValueError('unblocked priority decisions must not contain blockers')
        elif not self.blocking_alarm_identities:
            raise ValueError('blocked priority decisions require blocking alarms')


@dataclass(frozen=True, slots=True)
class GroupPriorityResolution:
    priority_group: str
    predominant_alarm_identity: AlarmIdentity | None
    alarms: tuple[AlarmPriorityDecision, ...]

    def __post_init__(self) -> None:
        _require_non_empty_string(self.priority_group, 'priority_group')
        if self.predominant_alarm_identity is not None and not isinstance(
            self.predominant_alarm_identity, AlarmIdentity
        ):
            raise TypeError('predominant_alarm_identity must be an AlarmIdentity')
        if not isinstance(self.alarms, tuple):
            raise TypeError('alarms must be a tuple')
        seen: set[AlarmIdentity] = set()
        predominant: list[AlarmIdentity] = []
        normalized: list[AlarmPriorityDecision] = []
        for decision in self.alarms:
            if not isinstance(decision, AlarmPriorityDecision):
                raise TypeError('alarms must contain AlarmPriorityDecision values')
            if decision.alarm_identity in seen:
                raise ValueError('priority resolution must not contain duplicate identities')
            seen.add(decision.alarm_identity)
            normalized.append(decision)
            if decision.disposition is PriorityDisposition.PREDOMINANT:
                predominant.append(decision.alarm_identity)
        if len(predominant) > 1:
            raise ValueError('priority resolution must contain at most one predominant alarm')
        expected = predominant[0] if predominant else None
        if self.predominant_alarm_identity != expected:
            raise ValueError('predominant_alarm_identity must match PREDOMINANT decision')
        object.__setattr__(self, 'alarms', tuple(sorted(normalized, key=lambda item: item.alarm_identity)))


@dataclass(frozen=True, slots=True)
class GroupLifecycleDecision:
    state: GroupLifecycleState
    occurrence_changes: tuple[OccurrenceChange, ...] = ()
    episode_changes: tuple[EpisodeChange, ...] = ()
    technical_hold_changes: tuple[TechnicalHoldChange, ...] = ()
    management_action_results: tuple[ManagementActionResult, ...] = ()
    management_effect_changes: tuple[ManagementEffectChange, ...] = ()
    deactivation_request_results: tuple[DeactivationRequestResult, ...] = ()
    deactivation_decision_results: tuple[DeactivationDecisionResult, ...] = ()
    deactivation_effect_changes: tuple[DeactivationEffectChange, ...] = ()
    reappearance_changes: tuple[ReappearanceChange, ...] = ()
    cascade_suppressions: tuple[CascadeSuppression, ...] = ()
    assignment_changes: tuple[AssignmentChange, ...] = ()
    priority_resolution: GroupPriorityResolution | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, GroupLifecycleState):
            raise TypeError('state must be a GroupLifecycleState')
        if not isinstance(self.occurrence_changes, tuple):
            raise TypeError('occurrence_changes must be a tuple')
        if not isinstance(self.episode_changes, tuple):
            raise TypeError('episode_changes must be a tuple')
        if not isinstance(self.technical_hold_changes, tuple):
            raise TypeError('technical_hold_changes must be a tuple')
        if not isinstance(self.management_action_results, tuple):
            raise TypeError('management_action_results must be a tuple')
        if not isinstance(self.management_effect_changes, tuple):
            raise TypeError('management_effect_changes must be a tuple')
        if not isinstance(self.deactivation_request_results, tuple):
            raise TypeError('deactivation_request_results must be a tuple')
        if not isinstance(self.deactivation_decision_results, tuple):
            raise TypeError('deactivation_decision_results must be a tuple')
        if not isinstance(self.deactivation_effect_changes, tuple):
            raise TypeError('deactivation_effect_changes must be a tuple')
        if not isinstance(self.reappearance_changes, tuple):
            raise TypeError('reappearance_changes must be a tuple')
        if not isinstance(self.cascade_suppressions, tuple):
            raise TypeError('cascade_suppressions must be a tuple')
        if not isinstance(self.assignment_changes, tuple):
            raise TypeError('assignment_changes must be a tuple')
        if self.priority_resolution is not None:
            if not isinstance(self.priority_resolution, GroupPriorityResolution):
                raise TypeError('priority_resolution must be a GroupPriorityResolution')
            if self.priority_resolution.priority_group != self.state.priority_group:
                raise ValueError('priority_resolution priority_group must match decision state')
        for change in self.occurrence_changes:
            if not isinstance(change, OccurrenceChange):
                raise TypeError('occurrence_changes must contain OccurrenceChange values')
        for change in self.episode_changes:
            if not isinstance(change, EpisodeChange):
                raise TypeError('episode_changes must contain EpisodeChange values')
        for change in self.technical_hold_changes:
            if not isinstance(change, TechnicalHoldChange):
                raise TypeError('technical_hold_changes must contain TechnicalHoldChange values')
        for result in self.management_action_results:
            if not isinstance(result, ManagementActionResult):
                raise TypeError(
                    'management_action_results must contain ManagementActionResult values'
                )
        for change in self.management_effect_changes:
            if not isinstance(change, ManagementEffectChange):
                raise TypeError(
                    'management_effect_changes must contain ManagementEffectChange values'
                )
        for result in self.deactivation_request_results:
            if not isinstance(result, DeactivationRequestResult):
                raise TypeError(
                    'deactivation_request_results must contain DeactivationRequestResult values'
                )
        for result in self.deactivation_decision_results:
            if not isinstance(result, DeactivationDecisionResult):
                raise TypeError(
                    'deactivation_decision_results must contain DeactivationDecisionResult values'
                )
        for change in self.deactivation_effect_changes:
            if not isinstance(change, DeactivationEffectChange):
                raise TypeError(
                    'deactivation_effect_changes must contain DeactivationEffectChange values'
                )
        for change in self.reappearance_changes:
            if not isinstance(change, ReappearanceChange):
                raise TypeError('reappearance_changes must contain ReappearanceChange values')
        for suppression in self.cascade_suppressions:
            if not isinstance(suppression, CascadeSuppression):
                raise TypeError('cascade_suppressions must contain CascadeSuppression values')
        for change in self.assignment_changes:
            if not isinstance(change, AssignmentChange):
                raise TypeError('assignment_changes must contain AssignmentChange values')

    @property
    def has_lifecycle_change(self) -> bool:
        return bool(
            self.occurrence_changes
            or self.episode_changes
            or self.technical_hold_changes
            or self.management_effect_changes
            or self.deactivation_effect_changes
            or self.reappearance_changes
            or self.assignment_changes
        )


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
