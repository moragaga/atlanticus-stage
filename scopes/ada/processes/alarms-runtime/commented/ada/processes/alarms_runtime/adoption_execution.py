# Consume un ConfigurationAdoptionPlan ya clasificado y materializa únicamente sus consecuencias operacionales durables.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ada.alarms.core import (
    ConfigurationClosure,
    GroupCommitMaterialization,
    GroupLifecycleDecision,
    OccurrenceClosureReason,
    materialize_group_commit,
    reconcile_group_configuration,
    resolve_group_priority,
    resolve_management_cascades,
)
from ada.alarms.persistence import CommitBatchResult
from ada.processes.alarms_runtime.adoption import (
    ConfigurationAdoptionChange,
    ConfigurationAdoptionDisposition,
    ConfigurationAdoptionPlan,
)
from ada.processes.alarms_runtime.composition import AlarmRuntimeComposition
from ada.processes.alarms_runtime.cycle import AlarmCommitTimeProvider
from ada.processes.alarms_runtime.session import AlarmExecutionSession
from atlanticus.runtime import JobRuntimeContext


# Contrato ConfigurationAdoptionExecutionError: agrupa datos y valida invariantes cerca de su frontera.
class ConfigurationAdoptionExecutionError(ValueError):
    pass


# Contrato ConfigurationAdoptionGroupResult: agrupa datos y valida invariantes cerca de su frontera.
@dataclass(frozen=True, slots=True)
class ConfigurationAdoptionGroupResult:
    priority_group: str
    decision: GroupLifecycleDecision
    materialization: GroupCommitMaterialization | None

    def __post_init__(self) -> None:
        if not isinstance(self.priority_group, str) or not self.priority_group.strip():
            raise ValueError('priority_group must be a non-empty string')
        if not isinstance(self.decision, GroupLifecycleDecision):
            raise TypeError('decision must be GroupLifecycleDecision')
        if self.decision.state.priority_group != self.priority_group:
            raise ConfigurationAdoptionExecutionError(
                'group decision priority_group must match adoption result priority_group'
            )
        if self.materialization is not None:
            if not isinstance(self.materialization, GroupCommitMaterialization):
                raise TypeError('materialization must be GroupCommitMaterialization or None')
            if self.materialization.commit.priority_group != self.priority_group:
                raise ConfigurationAdoptionExecutionError(
                    'group materialization priority_group must match adoption result priority_group'
                )


# Contrato ConfigurationAdoptionExecutionResult: agrupa datos y valida invariantes cerca de su frontera.
@dataclass(frozen=True, slots=True)
class ConfigurationAdoptionExecutionResult:
    plan: ConfigurationAdoptionPlan
    effective_at: datetime
    groups: tuple[ConfigurationAdoptionGroupResult, ...]
    commit_result: CommitBatchResult | None

    def __post_init__(self) -> None:
        if not isinstance(self.plan, ConfigurationAdoptionPlan):
            raise TypeError('plan must be ConfigurationAdoptionPlan')
        _require_utc_datetime(self.effective_at, 'effective_at')
        if not isinstance(self.groups, tuple) or not all(
            isinstance(item, ConfigurationAdoptionGroupResult) for item in self.groups
        ):
            raise TypeError('groups must contain ConfigurationAdoptionGroupResult values')
        group_keys = tuple(item.priority_group for item in self.groups)
        if group_keys != tuple(sorted(set(group_keys))):
            raise ConfigurationAdoptionExecutionError(
                'adoption group results must be unique and sorted by priority_group'
            )
        if self.commit_result is not None and not isinstance(self.commit_result, CommitBatchResult):
            raise TypeError('commit_result must be CommitBatchResult or None')
        materialization_count = len(self.materializations)
        if materialization_count == 0 and self.commit_result is not None:
            raise ConfigurationAdoptionExecutionError(
                'commit_result requires at least one adoption materialization'
            )
        if materialization_count > 0 and self.commit_result is None:
            raise ConfigurationAdoptionExecutionError(
                'adoption materializations require a commit_result'
            )
        if (
            self.commit_result is not None
            and self.commit_result.record_count != materialization_count
        ):
            raise ConfigurationAdoptionExecutionError(
                'commit_result record_count must match adoption materializations'
            )

    @property
    def session(self) -> AlarmExecutionSession:
        return self.plan.target.session

    @property
    def materializations(self) -> tuple[GroupCommitMaterialization, ...]:
        return tuple(
            group.materialization for group in self.groups if group.materialization is not None
        )


# Contrato AlarmConfigurationAdoptionExecutor: agrupa datos y valida invariantes cerca de su frontera.
@dataclass(slots=True)
class AlarmConfigurationAdoptionExecutor:
    composition: AlarmRuntimeComposition
    commit_time_provider: AlarmCommitTimeProvider
    runtime_artifact_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.composition, AlarmRuntimeComposition):
            raise TypeError('composition must be AlarmRuntimeComposition')
        if not isinstance(self.commit_time_provider, AlarmCommitTimeProvider):
            raise TypeError('commit_time_provider must implement AlarmCommitTimeProvider')
        if (
            not isinstance(self.runtime_artifact_version, str)
            or not self.runtime_artifact_version.strip()
        ):
            raise ValueError('runtime_artifact_version must be a non-empty string')
        self.runtime_artifact_version = self.runtime_artifact_version.strip()

    def execute(
        self,
        context: JobRuntimeContext,
        plan: ConfigurationAdoptionPlan,
        *,
        effective_at: datetime,
    ) -> ConfigurationAdoptionExecutionResult:
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be JobRuntimeContext')
        if not isinstance(plan, ConfigurationAdoptionPlan):
            raise TypeError('plan must be ConfigurationAdoptionPlan')
        _require_utc_datetime(effective_at, 'effective_at')
        if not plan.is_adoptable:
            reasons = ', '.join(
                f'{change.identity.canonical_key}:{change.rejection_reason.value}'
                for change in plan.rejected_changes
            )
            raise ConfigurationAdoptionExecutionError(
                f'configuration adoption plan is rejected: {reasons}'
            )
        if not self.composition.durability.persistence.read_head().aligned:
            raise ConfigurationAdoptionExecutionError(
                'Alarm Engine journal must be recovered before configuration adoption'
            )
        grouped_changes = self._grouped_changes(plan)
        if not grouped_changes:
            return ConfigurationAdoptionExecutionResult(
                plan=plan,
                effective_at=effective_at,
                groups=(),
                commit_result=None,
            )
        committed_at = self._committed_at(effective_at)
        groups = tuple(
            self._prepare_group(
                plan,
                priority_group=priority_group,
                changes=changes,
                effective_at=effective_at,
                committed_at=committed_at,
            )
            for priority_group, changes in sorted(grouped_changes.items())
        )
        materializations = tuple(
            group.materialization for group in groups if group.materialization is not None
        )
        commit_result = (
            None
            if not materializations
            else self.composition.commit_batch(context, materializations)
        )
        return ConfigurationAdoptionExecutionResult(
            plan=plan,
            effective_at=effective_at,
            groups=groups,
            commit_result=commit_result,
        )

    def _grouped_changes(
        self,
        plan: ConfigurationAdoptionPlan,
    ) -> dict[str, tuple[ConfigurationAdoptionChange, ...]]:
        grouped: dict[str, list[ConfigurationAdoptionChange]] = {}
        for change in plan.changes:
            if change.disposition is ConfigurationAdoptionDisposition.UNCHANGED:
                continue
            source_plan = plan.source.plan_for(change.identity)
            if source_plan is None:
                raise ConfigurationAdoptionExecutionError(
                    f'{change.identity.canonical_key}: source plan is missing during adoption'
                )
            grouped.setdefault(source_plan.priority_group, []).append(change)
        return {priority_group: tuple(changes) for priority_group, changes in grouped.items()}

    def _prepare_group(
        self,
        plan: ConfigurationAdoptionPlan,
        *,
        priority_group: str,
        changes: tuple[ConfigurationAdoptionChange, ...],
        effective_at: datetime,
        committed_at: datetime,
    ) -> ConfigurationAdoptionGroupResult:
        source_plans = tuple(
            entry.planned_alarm
            for entry in plan.source.session.entries
            if entry.planned_alarm.priority_group == priority_group
        )
        target_plans = tuple(
            entry.planned_alarm
            for entry in plan.target.session.entries
            if entry.planned_alarm.priority_group == priority_group
        )
        runtime_group = self.composition.load_group(
            priority_group,
            planned_alarms=source_plans,
        )
        previous_priority_resolution = resolve_group_priority(
            runtime_group.state,
            planned_alarms=source_plans,
            cascade_suppressions=resolve_management_cascades(
                runtime_group.state,
                planned_alarms=source_plans,
                at=effective_at,
            ),
        )
        closures = tuple(
            ConfigurationClosure(
                alarm_identity=change.identity,
                reason=(
                    OccurrenceClosureReason.CONFIGURATION_DISABLED
                    if change.disposition is ConfigurationAdoptionDisposition.DISABLED
                    else OccurrenceClosureReason.CONFIGURATION_REMOVED
                ),
                effective_at=effective_at,
            )
            for change in changes
            if change.disposition
            in {
                ConfigurationAdoptionDisposition.DISABLED,
                ConfigurationAdoptionDisposition.REMOVED,
            }
        )
        structural_reset = any(
            change.disposition is ConfigurationAdoptionDisposition.STRUCTURAL_RESET
            for change in changes
        )
        decision = reconcile_group_configuration(
            runtime_group.state,
            effective_at=effective_at,
            planned_alarms=target_plans,
            configuration_closures=closures,
            structural_reset=structural_reset,
        )
        materialization = materialize_group_commit(
            runtime_group.state,
            decision,
            evaluations=(),
            cycle_at=effective_at,
            committed_at=committed_at,
            alarm_configuration_revision=plan.target.alarm_configuration_revision,
            tool_registry_revision=plan.target.tool_registry_revision,
            runtime_artifact_version=self.runtime_artifact_version,
            previous_commit_id=runtime_group.last_commit_id,
            previous_priority_resolution=previous_priority_resolution,
        )
        return ConfigurationAdoptionGroupResult(
            priority_group=priority_group,
            decision=decision,
            materialization=materialization,
        )

    def _committed_at(self, effective_at: datetime) -> datetime:
        committed_at = self.commit_time_provider.committed_at(cycle_at=effective_at)
        if not isinstance(committed_at, datetime):
            raise TypeError('commit_time_provider must return datetime')
        if committed_at.tzinfo is None or committed_at.utcoffset() != timedelta(0):
            raise ValueError('committed_at must be timezone-aware UTC')
        if committed_at < effective_at:
            raise ValueError('committed_at must not be before effective_at')
        return committed_at


# Auxiliar _require_utc_datetime: mantiene una responsabilidad interna acotada y determinista.
def _require_utc_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f'{name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f'{name} must be timezone-aware UTC')
