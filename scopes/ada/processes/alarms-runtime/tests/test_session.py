from dataclasses import replace

import pytest

from ada.alarms.core import AlarmEvaluation, AlarmIdentity
from ada.data.core import (
    DataColumn,
    DataColumnType,
    DataPartition,
    DataRequirement,
    DataSource,
)
from ada.data.planner import DataPlanSchemaError
from ada.processes.alarms_runtime import (
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    AlarmExecutionSessionError,
    build_alarm_execution_session,
)
from tests.support import plan


def _evaluator(_context) -> AlarmEvaluation | None:
    return None


def _requirement(column: str, data_type: DataColumnType) -> DataRequirement:
    return DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(DataColumn(column, data_type),),
    )


def _contract(
    evaluator_key: str,
    *requirements: DataRequirement,
) -> AlarmEvaluatorContract:
    return AlarmEvaluatorContract(
        family_key='mill',
        evaluator_key=evaluator_key,
        evaluator=_evaluator,
        requirements=tuple(requirements),
    )


def test_session_freezes_revisions_and_builds_one_shared_data_plan() -> None:
    risk = replace(plan('risk', priority_order=2), evaluator_key='risk-threshold')
    impact = replace(plan('impact', priority_order=1), evaluator_key='impact-threshold')
    risk_requirement = _requirement('mill_pressure', DataColumnType.FLOAT)
    impact_requirement = _requirement('mill_temperature', DataColumnType.FLOAT)
    registry = AlarmEvaluatorRegistry(
        (
            _contract('risk-threshold', risk_requirement),
            _contract('impact-threshold', impact_requirement),
        )
    )

    session = build_alarm_execution_session(
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        planned_alarms=(risk, impact),
        evaluator_registry=registry,
    )

    assert session.alarm_configuration_revision == 'R42'
    assert session.tool_registry_revision == 'T18'
    assert session.planned_alarms == (risk, impact)
    assert session.identities == (risk.identity, impact.identity)
    assert session.data_plan.sources == (DataSource.PI_INTERPOLATED,)
    assert len(session.data_plan.views) == 1
    assert session.data_plan.views[0].column_names == ('mill_pressure', 'mill_temperature')
    assert session.data_plan.requirements_for(risk.identity.canonical_key) == (risk_requirement,)
    assert session.data_plan.requirements_for(impact.identity.canonical_key) == (
        impact_requirement,
    )


def test_session_rejects_conflicting_shared_column_types_before_io() -> None:
    first = replace(plan('risk', priority_order=2), evaluator_key='first')
    second = replace(plan('impact', priority_order=1), evaluator_key='second')
    registry = AlarmEvaluatorRegistry(
        (
            _contract('first', _requirement('shared_signal', DataColumnType.FLOAT)),
            _contract('second', _requirement('shared_signal', DataColumnType.TEXT)),
        )
    )

    with pytest.raises(DataPlanSchemaError, match='conflicting data types'):
        build_alarm_execution_session(
            alarm_configuration_revision='R42',
            tool_registry_revision='T18',
            planned_alarms=(first, second),
            evaluator_registry=registry,
        )


def test_session_rejects_mixed_alarm_configuration_revisions() -> None:
    planned = replace(plan(), alarm_configuration_revision='R43')
    registry = AlarmEvaluatorRegistry((_contract('threshold'),))

    with pytest.raises(AlarmExecutionSessionError, match='alarm configuration revision'):
        build_alarm_execution_session(
            alarm_configuration_revision='R42',
            tool_registry_revision='T18',
            planned_alarms=(planned,),
            evaluator_registry=registry,
        )


def test_session_rejects_mixed_tool_registry_revisions() -> None:
    planned = replace(plan(), tool_registry_revision='T19')
    registry = AlarmEvaluatorRegistry((_contract('threshold'),))

    with pytest.raises(AlarmExecutionSessionError, match='tool registry revision'):
        build_alarm_execution_session(
            alarm_configuration_revision='R42',
            tool_registry_revision='T18',
            planned_alarms=(planned,),
            evaluator_registry=registry,
        )


def test_registry_requires_unique_family_and_evaluator_key() -> None:
    with pytest.raises(ValueError, match='unique'):
        AlarmEvaluatorRegistry((_contract('threshold'), _contract('threshold')))


def test_unregistered_evaluator_is_structural_session_error() -> None:
    registry = AlarmEvaluatorRegistry(())

    with pytest.raises(AlarmExecutionSessionError, match='not registered'):
        build_alarm_execution_session(
            alarm_configuration_revision='R42',
            tool_registry_revision='T18',
            planned_alarms=(plan(),),
            evaluator_registry=registry,
        )


def test_session_copies_and_freezes_static_parameters() -> None:
    planned = plan()
    configured = {'limit': 12.5, 'mode': 'high', 'enabled': True}
    session = build_alarm_execution_session(
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        planned_alarms=(planned,),
        evaluator_registry=AlarmEvaluatorRegistry((_contract('threshold'),)),
        parameters_by_alarm={planned.identity: configured},
    )

    configured['limit'] = 99.0
    entry = session.entry_for(planned.identity)

    assert dict(entry.parameters) == {'limit': 12.5, 'mode': 'high', 'enabled': True}
    with pytest.raises(TypeError):
        entry.parameters['limit'] = 10.0  # type: ignore[index]


def test_parameters_for_alarm_outside_session_are_rejected() -> None:
    planned = plan()
    other = AlarmIdentity(family_key='mill', alarm_key='other')

    with pytest.raises(AlarmExecutionSessionError, match='outside the execution session'):
        build_alarm_execution_session(
            alarm_configuration_revision='R42',
            tool_registry_revision='T18',
            planned_alarms=(planned,),
            evaluator_registry=AlarmEvaluatorRegistry((_contract('threshold'),)),
            parameters_by_alarm={other: {'limit': 12.5}},
        )


def test_empty_session_keeps_explicit_revisions_without_data_views() -> None:
    session = build_alarm_execution_session(
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        planned_alarms=(),
        evaluator_registry=AlarmEvaluatorRegistry(()),
    )

    assert session.entries == ()
    assert session.data_plan.views == ()
    assert dict(session.data_plan.requirements_by_key) == {}
    assert session.alarm_configuration_revision == 'R42'
    assert session.tool_registry_revision == 'T18'


def test_session_build_does_not_execute_registered_evaluator() -> None:
    def must_not_run(_context):
        raise AssertionError('evaluator must not run while the session is being built')

    planned = plan()
    contract = AlarmEvaluatorContract(
        family_key='mill',
        evaluator_key='threshold',
        evaluator=must_not_run,
        requirements=(_requirement('mill_pressure', DataColumnType.FLOAT),),
    )

    session = build_alarm_execution_session(
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        planned_alarms=(planned,),
        evaluator_registry=AlarmEvaluatorRegistry((contract,)),
    )

    assert session.entry_for(planned.identity).evaluator is must_not_run
