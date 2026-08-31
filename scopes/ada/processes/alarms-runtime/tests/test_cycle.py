from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from ada.alarms.core import (
    AlarmEvaluation,
    AlarmStatus,
    EvaluationErrorOrigin,
    EvidenceContractRef,
    EvidenceSnapshot,
    PriorityDisposition,
)
from ada.data.core import (
    DataColumn,
    DataColumnType,
    DataPartition,
    DataRequirement,
    DataSource,
)
from ada.data.sources import (
    DataSourceLoadFailure,
    DataSourceRegistry,
    LoadedDataSources,
    LoadedDataSourceView,
    PiSourceProvider,
    build_current_source_registry,
)
from ada.processes.alarms_runtime import (
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    AlarmExecutionIteration,
    AlarmOperationalCycle,
    AlarmOperationalCycleError,
    build_alarm_execution_session,
    build_alarm_runtime_composition,
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


class CommitClock:
    def __init__(self, offset: timedelta | None = None) -> None:
        self.offset = timedelta(seconds=1) if offset is None else offset
        self.calls: list[datetime] = []

    def committed_at(self, *, cycle_at: datetime) -> datetime:
        self.calls.append(cycle_at)
        return cycle_at + self.offset


def _physical(alarm_key: str, status: AlarmStatus, *, at: datetime) -> AlarmEvaluation:
    return AlarmEvaluation(
        alarm_identity=identity(alarm_key),
        status=status,
        evaluated_at=at,
        evidence_snapshot=EvidenceSnapshot(
            contract_key='threshold',
            contract_version='v1',
            payload={'status': status.value},
        ),
    )


def _contract(alarm_key: str, evaluator, *requirements: DataRequirement):
    return AlarmEvaluatorContract(
        family_key='mill',
        evaluator_key=f'{alarm_key}-evaluator',
        evaluator=evaluator,
        requirements=tuple(requirements),
    )


def _planned(
    alarm_key: str,
    *,
    priority_order: int,
    priority_group: str = 'mill-feed',
    alarm_revision: str = 'R42',
):
    return replace(
        plan(alarm_key, priority_order=priority_order),
        evaluator_key=f'{alarm_key}-evaluator',
        priority_group=priority_group,
        alarm_configuration_revision=alarm_revision,
    )


def _session(*contracts_and_plans):
    plans = tuple(item[0] for item in contracts_and_plans)
    contracts = tuple(item[1] for item in contracts_and_plans)
    return build_alarm_execution_session(
        alarm_configuration_revision=plans[0].alarm_configuration_revision if plans else 'R42',
        tool_registry_revision='T18',
        planned_alarms=plans,
        evaluator_registry=AlarmEvaluatorRegistry(contracts),
    )


def _empty_iteration(session, *, at: datetime) -> AlarmExecutionIteration:
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


def _cycle(tmp_path: Path, session, ids: Ids | None = None, clock: CommitClock | None = None):
    generated = ids or Ids()
    commit_clock = clock or CommitClock()
    context = build_context(tmp_path)
    composition = build_alarm_runtime_composition(
        runtime_configuration=context.configuration,
    )
    cycle = AlarmOperationalCycle(
        session=session,
        composition=composition,
        occurrence_id_factory=generated.occurrence,
        episode_id_factory=generated.episode,
        commit_time_provider=commit_clock,
        runtime_artifact_version='ada-alarms-runtime/0.5.0',
        technical_evidence_contract=EvidenceContractRef(
            contract_key='evaluation-error',
            contract_version='v1',
        ),
    )
    return cycle, context, composition, generated, commit_clock


def test_data_driven_iteration_evaluates_materializes_commits_and_recovers(tmp_path: Path) -> None:
    requirement = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(DataColumn('pressure', DataColumnType.FLOAT),),
    )
    planned = _planned('risk', priority_order=1)

    def evaluator(context) -> AlarmEvaluation:
        value = context.data.get(
            DataSource.PI_INTERPOLATED,
            DataPartition.LATEST,
        ).last_value_number('pressure')
        return _physical(
            'risk',
            AlarmStatus.ACTIVE if value == 10.0 else AlarmStatus.INACTIVE,
            at=context.now,
        )

    session = _session((planned, _contract('risk', evaluator, requirement)))
    view = requirement.view
    registry = build_current_source_registry(pi_source=PiSourceProvider.NOTPII)
    iteration = AlarmExecutionIteration(
        session=session,
        loaded_sources=LoadedDataSources(
            as_of=NOW,
            plan=session.data_plan,
            registry=registry,
            loaded={
                view: LoadedDataSourceView(
                    view=view,
                    frame=pd.DataFrame({'pressure': [10.0]}),
                )
            },
            failures={},
        ),
    )
    cycle, context, composition, _, clock = _cycle(tmp_path, session)

    result = cycle.execute(context, iteration)
    recovered = composition.load_group('mill-feed', planned_alarms=(planned,))

    assert result.evaluation_for(planned.identity).status is AlarmStatus.ACTIVE
    assert len(result.materializations) == 1
    assert result.commit_result is not None and result.commit_result.record_count == 1
    assert recovered.last_commit_id == result.materializations[0].commit.commit_id
    recovered_alarm = recovered.state.get(planned.identity)
    assert recovered_alarm is not None and recovered_alarm.occurrence is not None
    assert recovered_alarm.occurrence.started_at == NOW
    assert clock.calls == [NOW]


def test_steady_cycle_without_durable_change_skips_commit_batch(tmp_path: Path) -> None:
    planned = _planned('risk', priority_order=1)
    session = _session(
        (
            planned,
            _contract(
                'risk',
                lambda context: _physical('risk', AlarmStatus.ACTIVE, at=context.now),
            ),
        )
    )
    cycle, context, composition, _, _ = _cycle(tmp_path, session)

    first = cycle.execute(context, _empty_iteration(session, at=NOW))
    second_at = NOW + timedelta(minutes=1)
    second = cycle.execute(context, _empty_iteration(session, at=second_at))
    snapshot = composition.durability.persistence.read_snapshot('mill-feed')

    assert len(first.materializations) == 1
    assert second.materializations == ()
    assert second.commit_result is None
    assert snapshot is not None
    assert snapshot.last_commit_id == first.materializations[0].commit.commit_id


def test_operational_cycle_uses_batch_group_hydration(tmp_path: Path, monkeypatch) -> None:
    first_plan = _planned('risk', priority_order=1, priority_group='group-a')
    second_plan = _planned('impact', priority_order=1, priority_group='group-b')
    session = _session(
        (
            first_plan,
            _contract(
                'risk',
                lambda context: _physical('risk', AlarmStatus.ACTIVE, at=context.now),
            ),
        ),
        (
            second_plan,
            _contract(
                'impact',
                lambda context: _physical('impact', AlarmStatus.ACTIVE, at=context.now),
            ),
        ),
    )
    cycle, context, composition, _, _ = _cycle(tmp_path, session)
    load_groups_calls = 0
    composition_type = type(composition)
    original_load_groups = composition_type.load_groups

    def counted_load_groups(self, planned_alarms_by_group):
        nonlocal load_groups_calls
        load_groups_calls += 1
        return original_load_groups(self, planned_alarms_by_group)

    monkeypatch.setattr(composition_type, 'load_groups', counted_load_groups)

    result = cycle.execute(context, _empty_iteration(session, at=NOW))

    assert load_groups_calls == 1
    assert len(result.materializations) == 2


def test_two_priority_groups_are_committed_in_one_batch(tmp_path: Path) -> None:
    first_plan = _planned('risk', priority_order=1, priority_group='group-a')
    second_plan = _planned('impact', priority_order=1, priority_group='group-b')
    session = _session(
        (
            first_plan,
            _contract(
                'risk',
                lambda context: _physical('risk', AlarmStatus.ACTIVE, at=context.now),
            ),
        ),
        (
            second_plan,
            _contract(
                'impact',
                lambda context: _physical('impact', AlarmStatus.ACTIVE, at=context.now),
            ),
        ),
    )
    cycle, context, _, _, _ = _cycle(tmp_path, session)

    result = cycle.execute(context, _empty_iteration(session, at=NOW))

    assert tuple(item.commit.priority_group for item in result.materializations) == (
        'group-a',
        'group-b',
    )
    assert result.commit_result is not None
    assert result.commit_result.record_count == 2


def test_evaluator_exception_isolated_to_one_alarm_and_other_alarm_commits(tmp_path: Path) -> None:
    risk = _planned('risk', priority_order=2)
    impact = _planned('impact', priority_order=1)

    def broken(_context):
        raise RuntimeError('private evaluator detail')

    session = _session(
        (risk, _contract('risk', broken)),
        (
            impact,
            _contract(
                'impact',
                lambda context: _physical('impact', AlarmStatus.ACTIVE, at=context.now),
            ),
        ),
    )
    cycle, context, _, _, _ = _cycle(tmp_path, session)

    result = cycle.execute(context, _empty_iteration(session, at=NOW))

    risk_evaluation = result.evaluation_for(risk.identity)
    assert risk_evaluation.status is AlarmStatus.ERROR
    assert risk_evaluation.error is not None
    assert risk_evaluation.error.origin is EvaluationErrorOrigin.EVALUATOR
    assert 'private evaluator detail' not in risk_evaluation.error.message
    assert result.evaluation_for(impact.identity).status is AlarmStatus.ACTIVE
    assert len(result.materializations) == 1


def test_failed_source_becomes_runtime_error_for_that_alarm_without_stopping_cycle(
    tmp_path: Path,
) -> None:
    requirement = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(DataColumn('pressure', DataColumnType.FLOAT),),
    )
    risk = _planned('risk', priority_order=2)
    impact = _planned('impact', priority_order=1)

    def must_not_run(_context):
        raise AssertionError('evaluator must not run when its data context cannot be prepared')

    session = _session(
        (risk, _contract('risk', must_not_run, requirement)),
        (
            impact,
            _contract(
                'impact',
                lambda context: _physical('impact', AlarmStatus.ACTIVE, at=context.now),
            ),
        ),
    )
    iteration = AlarmExecutionIteration(
        session=session,
        loaded_sources=LoadedDataSources(
            as_of=NOW,
            plan=session.data_plan,
            registry=DataSourceRegistry({}),
            loaded={},
            failures={
                requirement.view: DataSourceLoadFailure(
                    view=requirement.view,
                    message='source unavailable',
                )
            },
        ),
    )
    cycle, context, _, _, _ = _cycle(tmp_path, session)

    result = cycle.execute(context, iteration)

    risk_evaluation = result.evaluation_for(risk.identity)
    assert risk_evaluation.status is AlarmStatus.ERROR
    assert risk_evaluation.error is not None
    assert risk_evaluation.error.origin is EvaluationErrorOrigin.RUNTIME
    assert risk_evaluation.error.error_key == 'input_preparation_failed'
    assert risk_evaluation.error.message == 'Evaluation input could not be prepared'
    assert len(risk_evaluation.error.affected_inputs) == 1
    assert risk_evaluation.error.affected_inputs[0].source_key == DataSource.PI_INTERPOLATED.value
    assert result.evaluation_for(impact.identity).status is AlarmStatus.ACTIVE
    assert len(result.materializations) == 1


def test_previous_priority_is_rederived_to_materialize_suppression_journey(tmp_path: Path) -> None:
    phase = {'value': 1}
    risk = _planned('risk', priority_order=2)
    impact = _planned('impact', priority_order=1)

    def risk_evaluator(context) -> AlarmEvaluation:
        return _physical('risk', AlarmStatus.ACTIVE, at=context.now)

    def impact_evaluator(context) -> AlarmEvaluation:
        status = AlarmStatus.INACTIVE if phase['value'] == 1 else AlarmStatus.ACTIVE
        return _physical('impact', status, at=context.now)

    session = _session(
        (risk, _contract('risk', risk_evaluator)),
        (impact, _contract('impact', impact_evaluator)),
    )
    ids = Ids()
    cycle, context, _, _, _ = _cycle(tmp_path, session, ids=ids)
    cycle.execute(context, _empty_iteration(session, at=NOW))
    phase['value'] = 2

    result = cycle.execute(
        context,
        _empty_iteration(session, at=NOW + timedelta(minutes=1)),
    )

    materialization = result.materializations[0]
    event_keys = {event.event_key for event in materialization.records.journey_events}
    assert 'priority_suppressed' in event_keys
    resolution = result.group_for('mill-feed').decision.priority_resolution
    assert resolution is not None
    risk_priority = next(item for item in resolution.alarms if item.alarm_identity == risk.identity)
    assert risk_priority.disposition is PriorityDisposition.ECLIPSED


def test_cycle_rejects_iteration_from_another_session_before_execution(tmp_path: Path) -> None:
    planned = _planned('risk', priority_order=1)
    calls = {'count': 0}

    def evaluator(context) -> AlarmEvaluation:
        calls['count'] += 1
        return _physical('risk', AlarmStatus.ACTIVE, at=context.now)

    session = _session((planned, _contract('risk', evaluator)))
    foreign = _session((planned, _contract('risk', evaluator)))
    cycle, context, _, _, clock = _cycle(tmp_path, session)

    with pytest.raises(AlarmOperationalCycleError, match='execution session'):
        cycle.execute(context, _empty_iteration(foreign, at=NOW))

    assert calls['count'] == 0
    assert clock.calls == []


def test_cycle_accepts_older_state_basis_after_configuration_adoption_boundary(
    tmp_path: Path,
) -> None:
    old_plan = _planned('risk', priority_order=1, alarm_revision='R42')
    old_session = _session(
        (
            old_plan,
            _contract(
                'risk',
                lambda context: _physical('risk', AlarmStatus.ACTIVE, at=context.now),
            ),
        )
    )
    ids = Ids()
    old_cycle, context, composition, _, _ = _cycle(tmp_path, old_session, ids=ids)
    old_cycle.execute(context, _empty_iteration(old_session, at=NOW))

    new_plan = _planned('risk', priority_order=1, alarm_revision='R43')
    calls = {'count': 0}

    def new_evaluator(context) -> AlarmEvaluation:
        calls['count'] += 1
        return _physical('risk', AlarmStatus.ACTIVE, at=context.now)

    new_session = _session((new_plan, _contract('risk', new_evaluator)))
    new_cycle = AlarmOperationalCycle(
        session=new_session,
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

    result = new_cycle.execute(
        context,
        _empty_iteration(new_session, at=NOW + timedelta(minutes=1)),
    )

    assert calls['count'] == 1
    assert result.evaluations[0].status is AlarmStatus.ACTIVE
    snapshot = composition.durability.persistence.read_snapshot('mill-feed')
    assert snapshot is not None
    assert snapshot.as_document()['state_basis']['alarm_configuration_revision'] == 'R42'


def test_commit_time_provider_is_explicit_and_must_not_precede_cycle(tmp_path: Path) -> None:
    planned = _planned('risk', priority_order=1)
    session = _session(
        (
            planned,
            _contract(
                'risk',
                lambda context: _physical('risk', AlarmStatus.ACTIVE, at=context.now),
            ),
        )
    )
    clock = CommitClock(offset=timedelta(seconds=-1))
    cycle, context, composition, _, _ = _cycle(tmp_path, session, clock=clock)

    with pytest.raises(ValueError, match='before cycle_at'):
        cycle.execute(context, _empty_iteration(session, at=NOW))

    assert composition.durability.persistence.read_head().durable is None


def test_empty_session_executes_without_clock_or_persistence_work(tmp_path: Path) -> None:
    session = build_alarm_execution_session(
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        planned_alarms=(),
        evaluator_registry=AlarmEvaluatorRegistry(()),
    )
    cycle, context, _, _, clock = _cycle(tmp_path, session)

    result = cycle.execute(context, _empty_iteration(session, at=NOW))

    assert result.evaluations == ()
    assert result.groups == ()
    assert result.materializations == ()
    assert result.commit_result is None
    assert clock.calls == []


def test_input_failure_on_open_occurrence_starts_hold_with_explicit_technical_evidence(
    tmp_path: Path,
) -> None:
    requirement = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(DataColumn('pressure', DataColumnType.FLOAT),),
    )
    planned = _planned('risk', priority_order=1)

    def evaluator(context) -> AlarmEvaluation:
        context.data.get(DataSource.PI_INTERPOLATED, DataPartition.LATEST)
        return _physical('risk', AlarmStatus.ACTIVE, at=context.now)

    session = _session((planned, _contract('risk', evaluator, requirement)))
    cycle, context, _, _, _ = _cycle(tmp_path, session)
    first_at = NOW
    first = AlarmExecutionIteration(
        session=session,
        loaded_sources=LoadedDataSources(
            as_of=first_at,
            plan=session.data_plan,
            registry=build_current_source_registry(pi_source=PiSourceProvider.NOTPII),
            loaded={
                requirement.view: LoadedDataSourceView(
                    view=requirement.view,
                    frame=pd.DataFrame({'pressure': [10.0]}),
                )
            },
            failures={},
        ),
    )
    cycle.execute(context, first)
    error_at = NOW + timedelta(minutes=1)
    failed = AlarmExecutionIteration(
        session=session,
        loaded_sources=LoadedDataSources(
            as_of=error_at,
            plan=session.data_plan,
            registry=DataSourceRegistry({}),
            loaded={},
            failures={
                requirement.view: DataSourceLoadFailure(
                    view=requirement.view,
                    message='source unavailable',
                )
            },
        ),
    )

    result = cycle.execute(context, failed)

    materialization = result.materializations[0]
    runtime = materialization.state.get(planned.identity)
    assert runtime is not None and runtime.technical_hold is not None
    assert runtime.technical_hold.started_at == error_at
    evidence = materialization.records.evidence_records[0]
    assert evidence.evaluation.status is AlarmStatus.ERROR
    assert evidence.technical_contract == EvidenceContractRef(
        contract_key='evaluation-error',
        contract_version='v1',
    )
    assert 'technical_hold_started' in {
        event.event_key for event in materialization.records.journey_events
    }


def test_second_durable_change_for_same_group_and_as_of_fails_before_wal(tmp_path: Path) -> None:
    phase = {'active': True}
    planned = _planned('risk', priority_order=1)

    def evaluator(context) -> AlarmEvaluation:
        status = AlarmStatus.ACTIVE if phase['active'] else AlarmStatus.INACTIVE
        return _physical('risk', status, at=context.now)

    session = _session((planned, _contract('risk', evaluator)))
    cycle, context, composition, _, _ = _cycle(tmp_path, session)
    first = cycle.execute(context, _empty_iteration(session, at=NOW))
    phase['active'] = False

    with pytest.raises(AlarmOperationalCycleError, match='durable commit for iteration as_of'):
        cycle.execute(context, _empty_iteration(session, at=NOW))

    snapshot = composition.durability.persistence.read_snapshot('mill-feed')
    assert snapshot is not None
    assert snapshot.last_commit_id == first.materializations[0].commit.commit_id
