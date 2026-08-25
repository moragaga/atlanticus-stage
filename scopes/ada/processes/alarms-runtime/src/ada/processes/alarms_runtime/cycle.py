from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from ada.alarms.core import (
    DEFAULT_EVIDENCE_SAMPLING_INTERVAL_SECONDS,
    AffectedInputIssue,
    AlarmEvaluation,
    AlarmIdentity,
    AlarmStatus,
    EpisodeIdFactory,
    EvaluationContext,
    EvaluationError,
    EvaluationErrorOrigin,
    EvidenceContractRef,
    GroupCommitMaterialization,
    GroupLifecycleDecision,
    GroupPriorityResolution,
    OccurrenceIdFactory,
    execute_evaluator,
    materialize_group_commit,
    reduce_group_cycle,
    resolve_group_priority,
    resolve_management_cascades,
)
from ada.alarms.persistence import CommitBatchResult, GroupRuntimeSnapshot
from ada.data.sources import DataSourcesError, DataSourceUnavailableError
from ada.processes.alarms_runtime.composition import (
    AlarmRuntimeComposition,
    AlarmRuntimeGroup,
)
from ada.processes.alarms_runtime.iteration import AlarmExecutionIteration
from ada.processes.alarms_runtime.session import AlarmExecutionEntry, AlarmExecutionSession
from atlanticus.runtime import JobRuntimeContext


class AlarmOperationalCycleError(ValueError):
    pass


@runtime_checkable
class AlarmCommitTimeProvider(Protocol):
    def committed_at(self, *, cycle_at: datetime) -> datetime: ...


@dataclass(frozen=True, slots=True)
class AlarmGroupCycleResult:
    priority_group: str
    decision: GroupLifecycleDecision
    materialization: GroupCommitMaterialization | None

    def __post_init__(self) -> None:
        if not isinstance(self.priority_group, str) or not self.priority_group.strip():
            raise ValueError('priority_group must be a non-empty string')
        if not isinstance(self.decision, GroupLifecycleDecision):
            raise TypeError('decision must be GroupLifecycleDecision')
        if self.decision.state.priority_group != self.priority_group:
            raise AlarmOperationalCycleError(
                'group decision priority_group must match cycle result priority_group'
            )
        if self.materialization is not None:
            if not isinstance(self.materialization, GroupCommitMaterialization):
                raise TypeError('materialization must be GroupCommitMaterialization or None')
            if self.materialization.commit.priority_group != self.priority_group:
                raise AlarmOperationalCycleError(
                    'group materialization priority_group must match cycle result priority_group'
                )


@dataclass(frozen=True, slots=True)
class AlarmOperationalCycleResult:
    iteration: AlarmExecutionIteration
    evaluations: tuple[AlarmEvaluation, ...]
    groups: tuple[AlarmGroupCycleResult, ...]
    commit_result: CommitBatchResult | None

    def __post_init__(self) -> None:
        if not isinstance(self.iteration, AlarmExecutionIteration):
            raise TypeError('iteration must be AlarmExecutionIteration')
        if not isinstance(self.evaluations, tuple) or not all(
            isinstance(item, AlarmEvaluation) for item in self.evaluations
        ):
            raise TypeError('evaluations must contain AlarmEvaluation values')
        if not isinstance(self.groups, tuple) or not all(
            isinstance(item, AlarmGroupCycleResult) for item in self.groups
        ):
            raise TypeError('groups must contain AlarmGroupCycleResult values')
        evaluation_identities = tuple(item.alarm_identity for item in self.evaluations)
        if evaluation_identities != self.iteration.session.identities:
            raise AlarmOperationalCycleError(
                'evaluations must exactly follow the execution session alarm order'
            )
        expected_groups = tuple(
            sorted({entry.planned_alarm.priority_group for entry in self.iteration.session.entries})
        )
        actual_groups = tuple(item.priority_group for item in self.groups)
        if actual_groups != expected_groups:
            raise AlarmOperationalCycleError(
                'group results must exactly match execution session priority groups'
            )
        if self.commit_result is not None and not isinstance(self.commit_result, CommitBatchResult):
            raise TypeError('commit_result must be CommitBatchResult or None')
        materialization_count = len(self.materializations)
        if materialization_count == 0 and self.commit_result is not None:
            raise AlarmOperationalCycleError('commit_result requires at least one materialization')
        if materialization_count > 0 and self.commit_result is None:
            raise AlarmOperationalCycleError('materializations require a commit_result')
        if (
            self.commit_result is not None
            and self.commit_result.record_count != materialization_count
        ):
            raise AlarmOperationalCycleError(
                'commit_result record_count must match cycle materializations'
            )

    @property
    def materializations(self) -> tuple[GroupCommitMaterialization, ...]:
        return tuple(
            group.materialization for group in self.groups if group.materialization is not None
        )

    def evaluation_for(self, identity: AlarmIdentity) -> AlarmEvaluation:
        if not isinstance(identity, AlarmIdentity):
            raise TypeError('identity must be AlarmIdentity')
        for evaluation in self.evaluations:
            if evaluation.alarm_identity == identity:
                return evaluation
        raise AlarmOperationalCycleError(
            f'{identity.canonical_key}: evaluation is not part of the operational cycle result'
        )

    def group_for(self, priority_group: str) -> AlarmGroupCycleResult:
        if not isinstance(priority_group, str) or not priority_group.strip():
            raise ValueError('priority_group must be a non-empty string')
        for group in self.groups:
            if group.priority_group == priority_group:
                return group
        raise AlarmOperationalCycleError(
            f'{priority_group}: priority group is not part of the operational cycle result'
        )


@dataclass(frozen=True, slots=True)
class _PreparedGroup:
    priority_group: str
    runtime_group: AlarmRuntimeGroup
    evaluations: tuple[AlarmEvaluation, ...]
    decision: GroupLifecycleDecision
    previous_priority_resolution: GroupPriorityResolution


@dataclass(slots=True)
class AlarmOperationalCycle:
    session: AlarmExecutionSession
    composition: AlarmRuntimeComposition
    occurrence_id_factory: OccurrenceIdFactory
    episode_id_factory: EpisodeIdFactory
    commit_time_provider: AlarmCommitTimeProvider
    runtime_artifact_version: str
    technical_evidence_contract: EvidenceContractRef
    evidence_sampling_interval_seconds: int = DEFAULT_EVIDENCE_SAMPLING_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.session, AlarmExecutionSession):
            raise TypeError('session must be AlarmExecutionSession')
        if not isinstance(self.composition, AlarmRuntimeComposition):
            raise TypeError('composition must be AlarmRuntimeComposition')
        if not callable(self.occurrence_id_factory):
            raise TypeError('occurrence_id_factory must be callable')
        if not callable(self.episode_id_factory):
            raise TypeError('episode_id_factory must be callable')
        if not isinstance(self.commit_time_provider, AlarmCommitTimeProvider):
            raise TypeError('commit_time_provider must implement AlarmCommitTimeProvider')
        if (
            not isinstance(self.runtime_artifact_version, str)
            or not self.runtime_artifact_version.strip()
        ):
            raise ValueError('runtime_artifact_version must be a non-empty string')
        if not isinstance(self.technical_evidence_contract, EvidenceContractRef):
            raise TypeError('technical_evidence_contract must be EvidenceContractRef')
        if isinstance(self.evidence_sampling_interval_seconds, bool) or not isinstance(
            self.evidence_sampling_interval_seconds, int
        ):
            raise TypeError('evidence_sampling_interval_seconds must be an int')
        if self.evidence_sampling_interval_seconds <= 0:
            raise ValueError('evidence_sampling_interval_seconds must be greater than zero')
        self.runtime_artifact_version = self.runtime_artifact_version.strip()

    def execute(
        self,
        context: JobRuntimeContext,
        iteration: AlarmExecutionIteration,
    ) -> AlarmOperationalCycleResult:
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be JobRuntimeContext')
        if not isinstance(iteration, AlarmExecutionIteration):
            raise TypeError('iteration must be AlarmExecutionIteration')
        if iteration.session is not self.session:
            raise AlarmOperationalCycleError(
                'iteration must belong to the operational cycle execution session'
            )
        priority_groups = self._priority_groups()
        runtime_groups = {
            priority_group: self._load_group(priority_group) for priority_group in priority_groups
        }
        evaluations = tuple(
            self._evaluate_entry(iteration, entry) for entry in self.session.entries
        )
        prepared = tuple(
            self._prepare_group(
                priority_group,
                runtime_group=runtime_groups[priority_group],
                iteration=iteration,
                evaluations=evaluations,
            )
            for priority_group in priority_groups
        )
        if not prepared:
            return AlarmOperationalCycleResult(
                iteration=iteration,
                evaluations=evaluations,
                groups=(),
                commit_result=None,
            )
        committed_at = self._committed_at(iteration.as_of)
        groups = tuple(
            self._materialize_group(
                group,
                iteration=iteration,
                committed_at=committed_at,
            )
            for group in prepared
        )
        materializations = tuple(
            group.materialization for group in groups if group.materialization is not None
        )
        commit_result = (
            None
            if not materializations
            else self.composition.commit_batch(context, materializations)
        )
        return AlarmOperationalCycleResult(
            iteration=iteration,
            evaluations=evaluations,
            groups=groups,
            commit_result=commit_result,
        )

    def _evaluate_entry(
        self,
        iteration: AlarmExecutionIteration,
        entry: AlarmExecutionEntry,
    ) -> AlarmEvaluation:
        try:
            data = iteration.data_for(entry.identity)
        except DataSourcesError as error:
            return _input_error(entry, iteration, error)
        evaluation_context = EvaluationContext(
            alarm_identity=entry.identity,
            now=iteration.as_of,
            parameters=entry.parameters,
            data=data,
        )
        return execute_evaluator(entry.planned_alarm, evaluation_context, entry.evaluator)

    def _prepare_group(
        self,
        priority_group: str,
        *,
        runtime_group: AlarmRuntimeGroup,
        iteration: AlarmExecutionIteration,
        evaluations: Sequence[AlarmEvaluation],
    ) -> _PreparedGroup:
        plans = tuple(
            entry.planned_alarm
            for entry in self.session.entries
            if entry.planned_alarm.priority_group == priority_group
        )
        identities = {plan.identity for plan in plans}
        group_evaluations = tuple(
            evaluation for evaluation in evaluations if evaluation.alarm_identity in identities
        )
        previous_priority_resolution = resolve_group_priority(
            runtime_group.state,
            planned_alarms=plans,
            cascade_suppressions=resolve_management_cascades(
                runtime_group.state,
                planned_alarms=plans,
                at=iteration.as_of,
            ),
        )
        decision = reduce_group_cycle(
            runtime_group.state,
            cycle_at=iteration.as_of,
            planned_alarms=plans,
            evaluations=group_evaluations,
            occurrence_id_factory=self.occurrence_id_factory,
            episode_id_factory=self.episode_id_factory,
        )
        return _PreparedGroup(
            priority_group=priority_group,
            runtime_group=runtime_group,
            evaluations=group_evaluations,
            decision=decision,
            previous_priority_resolution=previous_priority_resolution,
        )

    def _load_group(self, priority_group: str) -> AlarmRuntimeGroup:
        plans = tuple(
            entry.planned_alarm
            for entry in self.session.entries
            if entry.planned_alarm.priority_group == priority_group
        )
        runtime_group = self.composition.load_group(priority_group, planned_alarms=plans)
        self._validate_state_basis(runtime_group.snapshot)
        return runtime_group

    def _materialize_group(
        self,
        group: _PreparedGroup,
        *,
        iteration: AlarmExecutionIteration,
        committed_at: datetime,
    ) -> AlarmGroupCycleResult:
        try:
            materialization = materialize_group_commit(
                group.runtime_group.state,
                group.decision,
                evaluations=group.evaluations,
                cycle_at=iteration.as_of,
                committed_at=committed_at,
                alarm_configuration_revision=self.session.alarm_configuration_revision,
                tool_registry_revision=self.session.tool_registry_revision,
                runtime_artifact_version=self.runtime_artifact_version,
                previous_commit_id=group.runtime_group.last_commit_id,
                evidence_sampling_interval_seconds=self.evidence_sampling_interval_seconds,
                technical_evidence_contract=self.technical_evidence_contract,
                previous_priority_resolution=group.previous_priority_resolution,
            )
        except ValueError as error:
            if str(error) != 'previous_commit_id must differ from commit_id':
                raise
            raise AlarmOperationalCycleError(
                'priority group already has a durable commit for iteration as_of'
            ) from error
        return AlarmGroupCycleResult(
            priority_group=group.priority_group,
            decision=group.decision,
            materialization=materialization,
        )

    def _committed_at(self, cycle_at: datetime) -> datetime:
        committed_at = self.commit_time_provider.committed_at(cycle_at=cycle_at)
        if not isinstance(committed_at, datetime):
            raise TypeError('commit_time_provider must return datetime')
        if committed_at.tzinfo is None or committed_at.utcoffset() != timedelta(0):
            raise ValueError('committed_at must be timezone-aware UTC')
        if committed_at < cycle_at:
            raise ValueError('committed_at must not be before cycle_at')
        return committed_at

    def _priority_groups(self) -> tuple[str, ...]:
        return tuple(sorted({entry.planned_alarm.priority_group for entry in self.session.entries}))

    def _validate_state_basis(self, snapshot: GroupRuntimeSnapshot | None) -> None:
        if snapshot is None:
            return
        document = snapshot.as_document()
        basis = document.get('state_basis')
        if basis is None:
            if document.get('episode') is not None or document['alarms']:
                raise AlarmOperationalCycleError(
                    'non-neutral group snapshot has no state_basis for revision validation'
                )
            return
        if basis['alarm_configuration_revision'] != self.session.alarm_configuration_revision:
            raise AlarmOperationalCycleError(
                'group snapshot alarm configuration revision requires controlled adoption'
            )
        if basis['tool_registry_revision'] != self.session.tool_registry_revision:
            raise AlarmOperationalCycleError(
                'group snapshot tool registry revision requires controlled adoption'
            )


def _input_error(
    entry: AlarmExecutionEntry,
    iteration: AlarmExecutionIteration,
    error: DataSourcesError,
) -> AlarmEvaluation:
    affected_inputs: tuple[AffectedInputIssue, ...] = ()
    if isinstance(error, DataSourceUnavailableError):
        affected_inputs = (
            AffectedInputIssue(
                source_key=error.source.value,
                reason_key='source_unavailable',
            ),
        )
    return AlarmEvaluation(
        alarm_identity=entry.identity,
        status=AlarmStatus.ERROR,
        evaluated_at=iteration.as_of,
        error=EvaluationError(
            origin=EvaluationErrorOrigin.RUNTIME,
            error_key='input_preparation_failed',
            message='Evaluation input could not be prepared',
            affected_inputs=affected_inputs,
        ),
    )
