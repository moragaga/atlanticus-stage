from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ada.alarms.core import (
    AlarmEvaluation,
    AlarmRouting,
    AlarmStatus,
    AssignmentChangeKind,
    Criticality,
    DeactivationIntent,
    DeactivationPolicy,
    EvidenceContractRef,
    EvidenceSnapshot,
    ManagementAction,
    OccurrenceClosureReason,
    RoutingDestination,
    ToolAssignment,
)
from ada.data.sources import DataSourceRegistry, LoadedDataSources
from ada.processes.alarms_runtime import (
    AlarmConfigurationAdoptionExecutor,
    AlarmConfigurationRevision,
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    AlarmExecutionIteration,
    AlarmOperationalCycle,
    AlarmOperationalInputs,
    ConfigurationAdoptionDisposition,
    ConfigurationAdoptionExecutionError,
    build_alarm_execution_session,
    build_alarm_runtime_composition,
    plan_configuration_adoption,
)
from tests.support import NOW, build_context, identity, plan


class Ids:
    def __init__(self) -> None:
        self.occurrences = 0
        self.episodes = 0

    def occurrence(self, _identity, _at) -> str:
        self.occurrences += 1
        return f'O{self.occurrences}'

    def episode(self, _priority_group, _at) -> str:
        self.episodes += 1
        return f'E{self.episodes}'

    def management_effect(self, _action) -> str:
        return 'ME1'

    def deactivation_request(self, _action) -> str:
        return 'DR1'

    def deactivation_effect(self, _request) -> str:
        return 'DE1'


class CommitClock:
    def committed_at(self, *, cycle_at: datetime) -> datetime:
        return cycle_at + timedelta(seconds=1)


def _evaluation(alarm_key: str, at: datetime) -> AlarmEvaluation:
    return AlarmEvaluation(
        alarm_identity=identity(alarm_key),
        status=AlarmStatus.ACTIVE,
        evaluated_at=at,
        evidence_snapshot=EvidenceSnapshot(
            contract_key='threshold',
            contract_version='v1',
            payload={'status': 'ACTIVE'},
        ),
    )


def _revision(
    revision: str,
    *,
    executable=(),
    defined=None,
    parameters=None,
) -> AlarmConfigurationRevision:
    plans = tuple(
        replace(
            item,
            alarm_configuration_revision=revision,
            tool_registry_revision='T18',
        )
        for item in executable
    )
    identities = tuple(item.identity for item in plans) if defined is None else tuple(defined)
    contracts = tuple(
        AlarmEvaluatorContract(
            family_key=item.identity.family_key,
            evaluator_key=item.evaluator_key,
            evaluator=lambda context, alarm_key=item.identity.alarm_key: _evaluation(
                alarm_key,
                context.now,
            ),
        )
        for item in plans
    )
    session = build_alarm_execution_session(
        alarm_configuration_revision=revision,
        tool_registry_revision='T18',
        planned_alarms=plans,
        evaluator_registry=AlarmEvaluatorRegistry(contracts),
        parameters_by_alarm=parameters,
    )
    return AlarmConfigurationRevision(
        alarm_configuration_revision=revision,
        tool_registry_revision='T18',
        defined_alarm_identities=identities,
        session=session,
    )


def _iteration(revision: AlarmConfigurationRevision, at: datetime) -> AlarmExecutionIteration:
    session = revision.session
    return AlarmExecutionIteration(
        session=session,
        loaded_sources=LoadedDataSources(
            as_of=at,
            plan=session.data_plan,
            registry=DataSourceRegistry({}),
            loaded={},
            failures={},
        ),
    )


def _runtime(tmp_path: Path, revision: AlarmConfigurationRevision):
    context = build_context(tmp_path)
    composition = build_alarm_runtime_composition(runtime_configuration=context.configuration)
    ids = Ids()
    cycle = AlarmOperationalCycle(
        session=revision.session,
        composition=composition,
        occurrence_id_factory=ids.occurrence,
        episode_id_factory=ids.episode,
        commit_time_provider=CommitClock(),
        runtime_artifact_version='ada-alarms-runtime/0.9.0',
        technical_evidence_contract=EvidenceContractRef(
            contract_key='evaluation-error',
            contract_version='v1',
        ),
    )
    cycle.execute(context, _iteration(revision, NOW))
    executor = AlarmConfigurationAdoptionExecutor(
        composition=composition,
        commit_time_provider=CommitClock(),
        runtime_artifact_version='ada-alarms-runtime/0.9.0',
    )
    return context, composition, ids, executor


def test_parameter_only_adoption_switches_session_without_artificial_commit(tmp_path: Path) -> None:
    alarm = replace(plan(), evaluator_key='risk-evaluator')
    source = _revision(
        'R42',
        executable=(alarm,),
        parameters={alarm.identity: {'threshold': 80.0}},
    )
    target = _revision(
        'R43',
        executable=(alarm,),
        parameters={alarm.identity: {'threshold': 85.0}},
    )
    context, composition, ids, executor = _runtime(tmp_path, source)
    before = composition.load_group('mill-feed', planned_alarms=source.session.planned_alarms)
    adoption = plan_configuration_adoption(source, target)

    result = executor.execute(
        context,
        adoption,
        effective_at=NOW + timedelta(minutes=1),
    )

    assert adoption.changes[0].disposition is ConfigurationAdoptionDisposition.COMPATIBLE
    assert result.session is target.session
    assert result.materializations == ()
    assert result.commit_result is None
    after = composition.load_group('mill-feed', planned_alarms=target.session.planned_alarms)
    assert after.last_commit_id == before.last_commit_id
    assert after.snapshot is not None
    assert after.snapshot.as_document()['state_basis']['alarm_configuration_revision'] == 'R42'

    next_cycle = AlarmOperationalCycle(
        session=target.session,
        composition=composition,
        occurrence_id_factory=ids.occurrence,
        episode_id_factory=ids.episode,
        commit_time_provider=CommitClock(),
        runtime_artifact_version='ada-alarms-runtime/0.9.0',
        technical_evidence_contract=EvidenceContractRef(
            contract_key='evaluation-error',
            contract_version='v1',
        ),
    )
    next_result = next_cycle.execute(
        context,
        _iteration(target, NOW + timedelta(minutes=2)),
    )
    assert next_result.evaluations[0].status is AlarmStatus.ACTIVE
    persisted = composition.load_group('mill-feed', planned_alarms=target.session.planned_alarms)
    assert persisted.snapshot is not None
    assert persisted.snapshot.as_document()['state_basis']['alarm_configuration_revision'] == 'R42'


def test_disabled_alarm_closes_occurrence_with_target_revision_commit(tmp_path: Path) -> None:
    alarm = replace(plan(), evaluator_key='risk-evaluator')
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(), defined=(alarm.identity,))
    context, composition, _, executor = _runtime(tmp_path, source)
    adoption = plan_configuration_adoption(source, target)
    effective_at = NOW + timedelta(minutes=1)

    result = executor.execute(context, adoption, effective_at=effective_at)

    group = result.groups[0]
    assert group.decision.occurrence_changes[0].occurrence.closure_reason is (
        OccurrenceClosureReason.CONFIGURATION_DISABLED
    )
    assert group.materialization is not None
    assert group.materialization.commit.alarm_configuration_revision == 'R43'
    assert result.commit_result is not None and result.commit_result.record_count == 1
    recovered = composition.load_group('mill-feed', planned_alarms=())
    assert recovered.state.get(alarm.identity) is None
    assert recovered.snapshot is not None
    assert 'state_basis' not in recovered.snapshot.as_document()


def test_adoption_replay_after_materialization_is_a_noop(tmp_path: Path) -> None:
    alarm = replace(plan(), evaluator_key='risk-evaluator')
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(), defined=(alarm.identity,))
    context, composition, _, executor = _runtime(tmp_path, source)
    adoption = plan_configuration_adoption(source, target)
    effective_at = NOW + timedelta(minutes=1)
    first = executor.execute(context, adoption, effective_at=effective_at)
    records_after_first = tuple(composition.durability.persistence.read_durable_records())

    replay = executor.execute(context, adoption, effective_at=effective_at)

    assert first.commit_result is not None
    assert replay.materializations == ()
    assert replay.commit_result is None
    assert tuple(composition.durability.persistence.read_durable_records()) == records_after_first


def test_c2_routing_adoption_reconciles_from_original_occurrence_start(tmp_path: Path) -> None:
    alarm = replace(
        plan(),
        evaluator_key='risk-evaluator',
        criticality=Criticality.C2,
        routing=AlarmRouting(
            origin_tool_key='io',
            destinations=(RoutingDestination(tool_key='strategic', delay_seconds=1800),),
        ),
    )
    source = _revision('R42', executable=(alarm,))
    target_alarm = replace(
        alarm,
        routing=AlarmRouting(
            origin_tool_key='io',
            destinations=(RoutingDestination(tool_key='strategic', delay_seconds=600),),
        ),
    )
    target = _revision('R43', executable=(target_alarm,))
    context, _, _, executor = _runtime(tmp_path, source)
    adoption = plan_configuration_adoption(source, target)
    effective_at = NOW + timedelta(minutes=20)

    result = executor.execute(context, adoption, effective_at=effective_at)

    runtime = result.groups[0].decision.state.get(alarm.identity)
    assert runtime is not None
    assert ToolAssignment('strategic', effective_at) in runtime.assignments
    assert runtime.pending_assignments == ()
    assert any(
        change.kind is AssignmentChangeKind.ASSIGNED
        and change.tool_key == 'strategic'
        and change.effective_at == effective_at
        for change in result.groups[0].decision.assignment_changes
    )


def test_structural_reset_closes_old_epoch_and_next_cycle_starts_new_occurrence(
    tmp_path: Path,
) -> None:
    alarm = replace(plan(), evaluator_key='risk-evaluator', criticality=Criticality.C3)
    source = _revision('R42', executable=(alarm,))
    target_alarm = replace(
        alarm,
        criticality=Criticality.C2,
        routing=AlarmRouting(
            origin_tool_key='io',
            destinations=(RoutingDestination(tool_key='strategic', delay_seconds=600),),
        ),
    )
    target = _revision('R43', executable=(target_alarm,))
    context, composition, ids, executor = _runtime(tmp_path, source)
    adoption = plan_configuration_adoption(source, target)
    effective_at = NOW + timedelta(minutes=1)

    result = executor.execute(context, adoption, effective_at=effective_at)

    group = result.groups[0]
    assert group.decision.state.get(alarm.identity) is None
    assert group.decision.occurrence_changes[0].occurrence.closure_reason is (
        OccurrenceClosureReason.CONFIGURATION_RECONFIGURED
    )
    target_cycle = AlarmOperationalCycle(
        session=target.session,
        composition=composition,
        occurrence_id_factory=ids.occurrence,
        episode_id_factory=ids.episode,
        commit_time_provider=CommitClock(),
        runtime_artifact_version='ada-alarms-runtime/0.9.0',
        technical_evidence_contract=EvidenceContractRef(
            contract_key='evaluation-error',
            contract_version='v1',
        ),
    )
    next_result = target_cycle.execute(
        context,
        _iteration(target, effective_at + timedelta(minutes=1)),
    )
    runtime = next_result.groups[0].decision.state.get(alarm.identity)
    assert runtime is not None and runtime.occurrence is not None
    assert runtime.occurrence.occurrence_id == 'O2'
    assert runtime.occurrence.alarm_configuration_revision == 'R43'


def test_removed_alarm_preserves_deactivation_until_later_cycle_expires_it(tmp_path: Path) -> None:
    alarm = replace(
        plan(),
        evaluator_key='risk-evaluator',
        deactivation_policy=DeactivationPolicy(approval_required=False),
    )
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(), defined=())
    context, composition, ids, executor = _runtime(tmp_path, source)
    current = composition.load_group('mill-feed', planned_alarms=source.session.planned_alarms)
    runtime = current.state.get(alarm.identity)
    assert runtime is not None and runtime.occurrence is not None
    deactivated_at = NOW + timedelta(minutes=1)
    effective_until = deactivated_at + timedelta(hours=1)
    deactivation_cycle = AlarmOperationalCycle(
        session=source.session,
        composition=composition,
        occurrence_id_factory=ids.occurrence,
        episode_id_factory=ids.episode,
        commit_time_provider=CommitClock(),
        runtime_artifact_version='ada-alarms-runtime/0.9.0',
        technical_evidence_contract=EvidenceContractRef(
            contract_key='evaluation-error',
            contract_version='v1',
        ),
        management_effect_id_factory=ids.management_effect,
        reappearance_due_at_resolver=lambda action: action.source_created_at + timedelta(hours=2),
        deactivation_request_id_factory=ids.deactivation_request,
        deactivation_effect_id_factory=ids.deactivation_effect,
    )
    deactivation_cycle.execute(
        context,
        _iteration(source, deactivated_at),
        operational_inputs=AlarmOperationalInputs(
            management_actions=(
                ManagementAction(
                    input_id='M1',
                    alarm_identity=alarm.identity,
                    source_occurrence_id=runtime.occurrence.occurrence_id,
                    tool_key='io',
                    actor_key='operator-a',
                    source_created_at=deactivated_at,
                    deactivation_intent=DeactivationIntent(effective_until=effective_until),
                ),
            ),
        ),
    )
    adoption = plan_configuration_adoption(source, target)
    adopted_at = deactivated_at + timedelta(minutes=1)

    result = executor.execute(context, adoption, effective_at=adopted_at)

    adopted = result.groups[0].decision.state.get(alarm.identity)
    assert adopted is not None
    assert adopted.occurrence is None
    assert adopted.management_effect is None
    assert adopted.deactivation_effect is not None
    assert adopted.deactivation_effect.effective_until == effective_until
    persisted = composition.load_group('mill-feed', planned_alarms=())
    orphan = persisted.state.get(alarm.identity)
    assert orphan is not None and orphan.deactivation_effect is not None

    target_cycle = AlarmOperationalCycle(
        session=target.session,
        composition=composition,
        occurrence_id_factory=ids.occurrence,
        episode_id_factory=ids.episode,
        commit_time_provider=CommitClock(),
        runtime_artifact_version='ada-alarms-runtime/0.9.0',
        technical_evidence_contract=EvidenceContractRef(
            contract_key='evaluation-error',
            contract_version='v1',
        ),
    )
    expiration = target_cycle.execute(context, _iteration(target, effective_until))

    expired_group = expiration.group_for('mill-feed')
    assert expired_group.decision.state.get(alarm.identity) is None
    assert len(expired_group.decision.deactivation_effect_changes) == 1
    assert expired_group.decision.deactivation_effect_changes[0].effective_at == effective_until
    assert expiration.commit_result is not None
    neutral = composition.load_group('mill-feed', planned_alarms=())
    assert neutral.state.alarms == ()
    assert neutral.snapshot is not None
    assert neutral.snapshot.as_document()['alarms'] == {}
    assert 'state_basis' not in neutral.snapshot.as_document()


def test_rejected_plan_fails_before_any_durable_mutation(tmp_path: Path) -> None:
    alarm = replace(plan(), evaluator_key='risk-evaluator')
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(replace(alarm, priority_group='other-group'),))
    context, composition, _, executor = _runtime(tmp_path, source)
    adoption = plan_configuration_adoption(source, target)
    records_before = tuple(composition.durability.persistence.read_durable_records())
    snapshot_before = composition.durability.persistence.read_snapshot('mill-feed')

    with pytest.raises(
        ConfigurationAdoptionExecutionError, match='configuration adoption plan is rejected'
    ):
        executor.execute(
            context,
            adoption,
            effective_at=NOW + timedelta(minutes=1),
        )

    assert tuple(composition.durability.persistence.read_durable_records()) == records_before
    assert composition.durability.persistence.read_snapshot('mill-feed') == snapshot_before
