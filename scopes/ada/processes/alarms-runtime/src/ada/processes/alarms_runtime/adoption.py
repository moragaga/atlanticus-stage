from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ada.alarms.core import AlarmIdentity, PlannedAlarm
from ada.processes.alarms_runtime.session import AlarmExecutionSession


class AlarmConfigurationRevisionError(ValueError):
    pass


class ConfigurationAdoptionPlanError(ValueError):
    pass


class ConfigurationAdoptionDisposition(StrEnum):
    UNCHANGED = 'unchanged'
    COMPATIBLE = 'compatible'
    DISABLED = 'disabled'
    REMOVED = 'removed'
    STRUCTURAL_RESET = 'structural_reset'
    REJECTED = 'rejected'


class ConfigurationAdoptionRejectionReason(StrEnum):
    PRIORITY_GROUP_CHANGED = 'priority_group_changed'
    ALARM_KIND_CHANGED = 'alarm_kind_changed'
    EVALUATOR_CHANGED = 'evaluator_changed'
    C1_ROUTING_MUTATION_UNSUPPORTED = 'c1_routing_mutation_unsupported'
    C3_ROUTING_MUTATION_UNSUPPORTED = 'c3_routing_mutation_unsupported'


@dataclass(frozen=True, slots=True)
class AlarmConfigurationRevision:
    alarm_configuration_revision: str
    tool_registry_revision: str
    defined_alarm_identities: tuple[AlarmIdentity, ...]
    session: AlarmExecutionSession

    def __post_init__(self) -> None:
        alarm_revision = _required_text(
            self.alarm_configuration_revision,
            'alarm_configuration_revision',
        )
        tool_revision = _required_text(self.tool_registry_revision, 'tool_registry_revision')
        identities = tuple(self.defined_alarm_identities)
        if not all(isinstance(item, AlarmIdentity) for item in identities):
            raise TypeError('defined_alarm_identities must contain AlarmIdentity values')
        if len(identities) != len(set(identities)):
            raise AlarmConfigurationRevisionError('defined alarm identities must be unique')
        if not isinstance(self.session, AlarmExecutionSession):
            raise TypeError('session must be AlarmExecutionSession')
        if self.session.alarm_configuration_revision != alarm_revision:
            raise AlarmConfigurationRevisionError(
                'execution session alarm configuration revision does not match revision'
            )
        if self.session.tool_registry_revision != tool_revision:
            raise AlarmConfigurationRevisionError(
                'execution session tool registry revision does not match revision'
            )
        defined = set(identities)
        executable = set(self.session.identities)
        if not executable <= defined:
            raise AlarmConfigurationRevisionError(
                'execution session alarms must be defined by the configuration revision'
            )
        object.__setattr__(self, 'alarm_configuration_revision', alarm_revision)
        object.__setattr__(self, 'tool_registry_revision', tool_revision)
        object.__setattr__(
            self,
            'defined_alarm_identities',
            tuple(sorted(identities, key=lambda item: item.canonical_key)),
        )

    @property
    def revision_key(self) -> tuple[str, str]:
        return self.alarm_configuration_revision, self.tool_registry_revision

    def is_defined(self, identity: AlarmIdentity) -> bool:
        _require_identity(identity)
        return identity in self.defined_alarm_identities

    def is_executable(self, identity: AlarmIdentity) -> bool:
        _require_identity(identity)
        return identity in self.session.identities

    def plan_for(self, identity: AlarmIdentity) -> PlannedAlarm | None:
        _require_identity(identity)
        for entry in self.session.entries:
            if entry.identity == identity:
                return entry.planned_alarm
        return None


@dataclass(frozen=True, slots=True)
class ConfigurationAdoptionChange:
    identity: AlarmIdentity
    disposition: ConfigurationAdoptionDisposition
    rejection_reason: ConfigurationAdoptionRejectionReason | None = None

    def __post_init__(self) -> None:
        _require_identity(self.identity)
        if not isinstance(self.disposition, ConfigurationAdoptionDisposition):
            raise TypeError('disposition must be ConfigurationAdoptionDisposition')
        if self.rejection_reason is not None and not isinstance(
            self.rejection_reason, ConfigurationAdoptionRejectionReason
        ):
            raise TypeError('rejection_reason must be ConfigurationAdoptionRejectionReason or None')
        if self.disposition is ConfigurationAdoptionDisposition.REJECTED:
            if self.rejection_reason is None:
                raise ConfigurationAdoptionPlanError(
                    'rejected configuration change requires rejection_reason'
                )
        elif self.rejection_reason is not None:
            raise ConfigurationAdoptionPlanError(
                'rejection_reason is only valid for rejected configuration changes'
            )


@dataclass(frozen=True, slots=True)
class ConfigurationAdoptionPlan:
    source: AlarmConfigurationRevision
    target: AlarmConfigurationRevision
    changes: tuple[ConfigurationAdoptionChange, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source, AlarmConfigurationRevision):
            raise TypeError('source must be AlarmConfigurationRevision')
        if not isinstance(self.target, AlarmConfigurationRevision):
            raise TypeError('target must be AlarmConfigurationRevision')
        if self.source.revision_key == self.target.revision_key:
            raise ConfigurationAdoptionPlanError('source and target revisions must differ')
        changes = tuple(self.changes)
        if not all(isinstance(item, ConfigurationAdoptionChange) for item in changes):
            raise TypeError('changes must contain ConfigurationAdoptionChange values')
        identities = tuple(change.identity for change in changes)
        if len(identities) != len(set(identities)):
            raise ConfigurationAdoptionPlanError('configuration changes must be unique by identity')
        if set(identities) != set(self.source.session.identities):
            raise ConfigurationAdoptionPlanError(
                'configuration changes must exactly cover source execution session alarms'
            )
        for change in changes:
            self._validate_change(change)
        object.__setattr__(
            self,
            'changes',
            tuple(sorted(changes, key=lambda item: item.identity.canonical_key)),
        )

    @property
    def is_adoptable(self) -> bool:
        return all(
            change.disposition is not ConfigurationAdoptionDisposition.REJECTED
            for change in self.changes
        )

    @property
    def rejected_changes(self) -> tuple[ConfigurationAdoptionChange, ...]:
        return tuple(
            change
            for change in self.changes
            if change.disposition is ConfigurationAdoptionDisposition.REJECTED
        )

    @property
    def structural_reset_groups(self) -> tuple[str, ...]:
        groups = {
            self.source.plan_for(change.identity).priority_group
            for change in self.changes
            if change.disposition is ConfigurationAdoptionDisposition.STRUCTURAL_RESET
        }
        return tuple(sorted(groups))

    def _validate_change(self, change: ConfigurationAdoptionChange) -> None:
        source_plan = self.source.plan_for(change.identity)
        if source_plan is None:
            raise ConfigurationAdoptionPlanError(
                f'{change.identity.canonical_key}: source execution plan is missing'
            )
        target_plan = self.target.plan_for(change.identity)
        target_defined = self.target.is_defined(change.identity)
        if change.disposition in {
            ConfigurationAdoptionDisposition.UNCHANGED,
            ConfigurationAdoptionDisposition.COMPATIBLE,
            ConfigurationAdoptionDisposition.STRUCTURAL_RESET,
            ConfigurationAdoptionDisposition.REJECTED,
        }:
            if target_plan is None:
                raise ConfigurationAdoptionPlanError(
                    f'{change.identity.canonical_key}: target execution plan is required for '
                    f'{change.disposition.value}'
                )
            return
        if target_plan is not None:
            raise ConfigurationAdoptionPlanError(
                f'{change.identity.canonical_key}: {change.disposition.value} alarm must not be '
                'executable in target revision'
            )
        if change.disposition is ConfigurationAdoptionDisposition.DISABLED and not target_defined:
            raise ConfigurationAdoptionPlanError(
                f'{change.identity.canonical_key}: disabled alarm must remain defined in target '
                'revision'
            )
        if change.disposition is ConfigurationAdoptionDisposition.REMOVED and target_defined:
            raise ConfigurationAdoptionPlanError(
                f'{change.identity.canonical_key}: removed alarm must not be defined in target '
                'revision'
            )


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f'{field_name} must be a string')
    normalized = value.strip()
    if not normalized:
        raise ValueError(f'{field_name} must not be empty')
    return normalized


def _require_identity(identity: AlarmIdentity) -> None:
    if not isinstance(identity, AlarmIdentity):
        raise TypeError('identity must be AlarmIdentity')
