from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from ada.alarms.core import AlarmIdentity, Evaluator, PlannedAlarm
from ada.data.core import DataRequirement
from ada.data.planner import DataLoadPlan, DataRequirementPlanner

AlarmParameterValue = str | float | bool


class AlarmExecutionSessionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AlarmEvaluatorContract:
    family_key: str
    evaluator_key: str
    evaluator: Evaluator
    requirements: tuple[DataRequirement, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, 'family_key', _required_text(self.family_key, 'family_key'))
        object.__setattr__(
            self, 'evaluator_key', _required_text(self.evaluator_key, 'evaluator_key')
        )
        if not callable(self.evaluator):
            raise TypeError('evaluator must be callable')
        requirements = tuple(self.requirements)
        if not all(isinstance(item, DataRequirement) for item in requirements):
            raise TypeError('requirements must contain DataRequirement values')
        object.__setattr__(self, 'requirements', requirements)

    @property
    def key(self) -> tuple[str, str]:
        return self.family_key, self.evaluator_key


@dataclass(frozen=True, slots=True)
class AlarmEvaluatorRegistry:
    contracts: tuple[AlarmEvaluatorContract, ...]

    def __post_init__(self) -> None:
        contracts = tuple(self.contracts)
        if not all(isinstance(item, AlarmEvaluatorContract) for item in contracts):
            raise TypeError('contracts must contain AlarmEvaluatorContract values')
        keys = tuple(contract.key for contract in contracts)
        if len(keys) != len(set(keys)):
            raise ValueError('evaluator contracts must be unique by family_key and evaluator_key')
        object.__setattr__(self, 'contracts', contracts)

    def resolve(self, planned_alarm: PlannedAlarm) -> AlarmEvaluatorContract:
        if not isinstance(planned_alarm, PlannedAlarm):
            raise TypeError('planned_alarm must be PlannedAlarm')
        key = planned_alarm.identity.family_key, planned_alarm.evaluator_key
        for contract in self.contracts:
            if contract.key == key:
                return contract
        raise AlarmExecutionSessionError(
            f'{planned_alarm.identity.canonical_key}: evaluator contract is not registered: '
            f'{key[0]}/{key[1]}'
        )


@dataclass(frozen=True, slots=True)
class AlarmExecutionEntry:
    planned_alarm: PlannedAlarm
    evaluator_contract: AlarmEvaluatorContract
    parameters: Mapping[str, AlarmParameterValue]

    def __post_init__(self) -> None:
        if not isinstance(self.planned_alarm, PlannedAlarm):
            raise TypeError('planned_alarm must be PlannedAlarm')
        if not isinstance(self.evaluator_contract, AlarmEvaluatorContract):
            raise TypeError('evaluator_contract must be AlarmEvaluatorContract')
        expected_key = self.planned_alarm.identity.family_key, self.planned_alarm.evaluator_key
        if self.evaluator_contract.key != expected_key:
            raise AlarmExecutionSessionError(
                f'{self.planned_alarm.identity.canonical_key}: evaluator contract does not match '
                'planned alarm'
            )
        object.__setattr__(self, 'parameters', _normalize_parameters(self.parameters))

    @property
    def identity(self) -> AlarmIdentity:
        return self.planned_alarm.identity

    @property
    def evaluator(self) -> Evaluator:
        return self.evaluator_contract.evaluator

    @property
    def requirements(self) -> tuple[DataRequirement, ...]:
        return self.evaluator_contract.requirements


@dataclass(frozen=True, slots=True)
class AlarmExecutionSession:
    alarm_configuration_revision: str
    tool_registry_revision: str
    entries: tuple[AlarmExecutionEntry, ...]
    data_plan: DataLoadPlan

    def __post_init__(self) -> None:
        alarm_revision = _required_text(
            self.alarm_configuration_revision,
            'alarm_configuration_revision',
        )
        tool_revision = _required_text(self.tool_registry_revision, 'tool_registry_revision')
        entries = tuple(self.entries)
        if not all(isinstance(item, AlarmExecutionEntry) for item in entries):
            raise TypeError('entries must contain AlarmExecutionEntry values')
        identities = tuple(entry.identity for entry in entries)
        if len(identities) != len(set(identities)):
            raise AlarmExecutionSessionError('execution session alarm identities must be unique')
        if not isinstance(self.data_plan, DataLoadPlan):
            raise TypeError('data_plan must be DataLoadPlan')
        expected_keys = {identity.canonical_key for identity in identities}
        actual_keys = set(self.data_plan.requirements_by_key)
        if actual_keys != expected_keys:
            raise AlarmExecutionSessionError(
                'data plan consumers must exactly match execution session alarms'
            )
        for entry in entries:
            plan = entry.planned_alarm
            if plan.alarm_configuration_revision != alarm_revision:
                raise AlarmExecutionSessionError(
                    f'{entry.identity.canonical_key}: alarm configuration revision does not match '
                    'execution session'
                )
            if plan.tool_registry_revision != tool_revision:
                raise AlarmExecutionSessionError(
                    f'{entry.identity.canonical_key}: tool registry revision does not match '
                    'execution session'
                )
            if self.data_plan.requirements_for(entry.identity.canonical_key) != entry.requirements:
                raise AlarmExecutionSessionError(
                    f'{entry.identity.canonical_key}: data plan requirements do not match evaluator '
                    'contract'
                )
        object.__setattr__(self, 'alarm_configuration_revision', alarm_revision)
        object.__setattr__(self, 'tool_registry_revision', tool_revision)
        object.__setattr__(self, 'entries', entries)

    @property
    def planned_alarms(self) -> tuple[PlannedAlarm, ...]:
        return tuple(entry.planned_alarm for entry in self.entries)

    @property
    def identities(self) -> tuple[AlarmIdentity, ...]:
        return tuple(entry.identity for entry in self.entries)

    def entry_for(self, identity: AlarmIdentity) -> AlarmExecutionEntry:
        if not isinstance(identity, AlarmIdentity):
            raise TypeError('identity must be AlarmIdentity')
        for entry in self.entries:
            if entry.identity == identity:
                return entry
        raise AlarmExecutionSessionError(
            f'{identity.canonical_key}: alarm is not part of the execution session'
        )


def build_alarm_execution_session(
    *,
    alarm_configuration_revision: str,
    tool_registry_revision: str,
    planned_alarms: Sequence[PlannedAlarm],
    evaluator_registry: AlarmEvaluatorRegistry,
    parameters_by_alarm: Mapping[AlarmIdentity, Mapping[str, AlarmParameterValue]] | None = None,
) -> AlarmExecutionSession:
    alarm_revision = _required_text(
        alarm_configuration_revision,
        'alarm_configuration_revision',
    )
    tool_revision = _required_text(tool_registry_revision, 'tool_registry_revision')
    if isinstance(planned_alarms, str | bytes) or not isinstance(planned_alarms, Sequence):
        raise TypeError('planned_alarms must be a sequence')
    plans = tuple(planned_alarms)
    if not all(isinstance(item, PlannedAlarm) for item in plans):
        raise TypeError('planned_alarms must contain PlannedAlarm values')
    identities = tuple(plan.identity for plan in plans)
    if len(identities) != len(set(identities)):
        raise AlarmExecutionSessionError('planned_alarms must contain unique alarm identities')
    if not isinstance(evaluator_registry, AlarmEvaluatorRegistry):
        raise TypeError('evaluator_registry must be AlarmEvaluatorRegistry')
    parameters = _normalize_parameters_by_alarm(parameters_by_alarm, identities=identities)
    entries: list[AlarmExecutionEntry] = []
    requirements_by_key: dict[str, tuple[DataRequirement, ...]] = {}
    for plan in plans:
        if plan.alarm_configuration_revision != alarm_revision:
            raise AlarmExecutionSessionError(
                f'{plan.identity.canonical_key}: alarm configuration revision does not match '
                'execution session'
            )
        if plan.tool_registry_revision != tool_revision:
            raise AlarmExecutionSessionError(
                f'{plan.identity.canonical_key}: tool registry revision does not match execution '
                'session'
            )
        contract = evaluator_registry.resolve(plan)
        entry = AlarmExecutionEntry(
            planned_alarm=plan,
            evaluator_contract=contract,
            parameters=parameters.get(plan.identity, MappingProxyType({})),
        )
        entries.append(entry)
        requirements_by_key[plan.identity.canonical_key] = entry.requirements
    data_plan = DataRequirementPlanner().plan(requirements_by_key)
    return AlarmExecutionSession(
        alarm_configuration_revision=alarm_revision,
        tool_registry_revision=tool_revision,
        entries=tuple(entries),
        data_plan=data_plan,
    )


def _normalize_parameters_by_alarm(
    values: Mapping[AlarmIdentity, Mapping[str, AlarmParameterValue]] | None,
    *,
    identities: tuple[AlarmIdentity, ...],
) -> Mapping[AlarmIdentity, Mapping[str, AlarmParameterValue]]:
    if values is None:
        return MappingProxyType({})
    if not isinstance(values, Mapping):
        raise TypeError('parameters_by_alarm must be a mapping or None')
    allowed = set(identities)
    normalized: dict[AlarmIdentity, Mapping[str, AlarmParameterValue]] = {}
    for identity, parameters in values.items():
        if not isinstance(identity, AlarmIdentity):
            raise TypeError('parameters_by_alarm keys must be AlarmIdentity values')
        if identity not in allowed:
            raise AlarmExecutionSessionError(
                f'{identity.canonical_key}: parameters were provided for an alarm outside the '
                'execution session'
            )
        normalized[identity] = _normalize_parameters(parameters)
    return MappingProxyType(normalized)


def _normalize_parameters(
    values: Mapping[str, AlarmParameterValue],
) -> Mapping[str, AlarmParameterValue]:
    if not isinstance(values, Mapping):
        raise TypeError('parameters must be a mapping')
    normalized: dict[str, AlarmParameterValue] = {}
    for key, value in values.items():
        normalized_key = _required_text(key, 'parameter key')
        if not isinstance(value, bool | str | float):
            raise TypeError('parameter values must be TEXT, FLOAT, or BOOLEAN')
        normalized[normalized_key] = value
    return MappingProxyType(normalized)


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value
