from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd
import pyarrow as pa
import pytest

from ada.alarms.core import AlarmEvaluation, AlarmIdentity
from ada.data.core import DataColumn, DataColumnType, DataPartition, DataRequirement, DataSource
from ada.data.planner import DataLoadPlan
from ada.data.sources import (
    DataSourceLoader,
    DataSourceRegistry,
    LoadedDataSources,
    PiSourceProvider,
    build_current_source_registry,
)
from ada.processes.alarms_runtime import (
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    AlarmExecutionIterationError,
    AlarmExecutionSessionError,
    AlarmIterationLoader,
    build_alarm_execution_session,
)
from tests.support import plan


class IncrementingReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def read_frame(
        self,
        *,
        definition,
        target,
        projection_schema,
        timestamp_column=None,
        start_utc=None,
        end_utc=None,
    ):
        self.calls.append((target.identifier, tuple(projection_schema.names)))
        generation = len(self.calls)
        values = {}
        for field in projection_schema:
            if pa.types.is_timestamp(field.type):
                values[field.name] = [datetime(2026, 8, 24, 12, 0, tzinfo=UTC)]
            elif pa.types.is_floating(field.type):
                values[field.name] = [float(generation)]
            elif pa.types.is_integer(field.type):
                values[field.name] = [generation]
            elif pa.types.is_boolean(field.type):
                values[field.name] = [True]
            else:
                values[field.name] = [f'value-{generation}']
        return pd.DataFrame(values)


class RecordingLoader:
    def __init__(self, factory) -> None:
        self._factory = factory
        self.calls: list[tuple[DataLoadPlan, datetime]] = []

    def load(self, *, plan: DataLoadPlan, as_of: datetime) -> LoadedDataSources:
        self.calls.append((plan, as_of))
        return self._factory(plan, as_of)


def _evaluator(_context) -> AlarmEvaluation | None:
    return None


def _requirement(column: str) -> DataRequirement:
    return DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(DataColumn(column, DataColumnType.FLOAT),),
    )


def _session(*alarm_columns: tuple[str, str]):
    planned = []
    contracts = []
    for index, (alarm_key, column) in enumerate(alarm_columns, start=1):
        evaluator_key = f'{alarm_key}-evaluator'
        planned.append(
            replace(
                plan(alarm_key, priority_order=index),
                evaluator_key=evaluator_key,
            )
        )
        contracts.append(
            AlarmEvaluatorContract(
                family_key='mill',
                evaluator_key=evaluator_key,
                evaluator=_evaluator,
                requirements=(_requirement(column),),
            )
        )
    return build_alarm_execution_session(
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        planned_alarms=tuple(planned),
        evaluator_registry=AlarmEvaluatorRegistry(tuple(contracts)),
    )


def _real_iteration_loader(session):
    reader = IncrementingReader()
    source_loader = DataSourceLoader(
        reader=reader,
        registry=build_current_source_registry(pi_source=PiSourceProvider.NOTPII),
    )
    return AlarmIterationLoader(session=session, source_loader=source_loader), reader


def _empty_loaded(plan: DataLoadPlan, as_of: datetime) -> LoadedDataSources:
    return LoadedDataSources(
        as_of=as_of,
        plan=plan,
        registry=DataSourceRegistry({}),
        loaded={},
        failures={},
    )


def test_each_iteration_reloads_sources_even_when_as_of_is_unchanged() -> None:
    session = _session(('risk', 'pressure'))
    loader, reader = _real_iteration_loader(session)
    as_of = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)

    first = loader.load(as_of=as_of)
    second = loader.load(as_of=as_of)

    first_value = (
        first.data_for(session.identities[0])
        .get(
            DataSource.PI_INTERPOLATED,
            DataPartition.LATEST,
        )
        .last_value_number('pressure')
    )
    second_value = (
        second.data_for(session.identities[0])
        .get(
            DataSource.PI_INTERPOLATED,
            DataPartition.LATEST,
        )
        .last_value_number('pressure')
    )
    assert len(reader.calls) == 2
    assert first_value == 1.0
    assert second_value == 2.0
    assert first.loaded_sources is not second.loaded_sources
    assert first.loaded_sources.plan is session.data_plan
    assert second.loaded_sources.plan is session.data_plan


def test_shared_source_view_is_read_once_per_iteration_for_all_alarms() -> None:
    session = _session(('risk', 'pressure'), ('impact', 'temperature'))
    loader, reader = _real_iteration_loader(session)

    iteration = loader.load(as_of=datetime(2026, 8, 24, 12, 0, tzinfo=UTC))

    risk = iteration.data_for(session.identities[0]).get(
        DataSource.PI_INTERPOLATED,
        DataPartition.LATEST,
    )
    impact = iteration.data_for(session.identities[1]).get(
        DataSource.PI_INTERPOLATED,
        DataPartition.LATEST,
    )
    assert len(reader.calls) == 1
    assert reader.calls[0][1] == ('pressure', 'temperature', 'timestamp_utc')
    assert list(risk.dataframe.columns) == ['pressure']
    assert list(impact.dataframe.columns) == ['temperature']


def test_iteration_passes_each_explicit_as_of_to_the_source_loader() -> None:
    session = build_alarm_execution_session(
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        planned_alarms=(),
        evaluator_registry=AlarmEvaluatorRegistry(()),
    )
    loader = RecordingLoader(_empty_loaded)
    iteration_loader = AlarmIterationLoader(session=session, source_loader=loader)
    first_as_of = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    second_as_of = datetime(2026, 8, 24, 12, 1, tzinfo=UTC)

    first = iteration_loader.load(as_of=first_as_of)
    second = iteration_loader.load(as_of=second_as_of)

    assert loader.calls == [
        (session.data_plan, first_as_of),
        (session.data_plan, second_as_of),
    ]
    assert first.as_of == first_as_of
    assert second.as_of == second_as_of


def test_iteration_loading_does_not_execute_registered_evaluator() -> None:
    def must_not_run(_context):
        raise AssertionError('evaluator must not run while iteration inputs are loaded')

    planned = plan()
    session = build_alarm_execution_session(
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        planned_alarms=(planned,),
        evaluator_registry=AlarmEvaluatorRegistry(
            (
                AlarmEvaluatorContract(
                    family_key='mill',
                    evaluator_key='threshold',
                    evaluator=must_not_run,
                    requirements=(_requirement('pressure'),),
                ),
            )
        ),
    )
    loader, reader = _real_iteration_loader(session)

    iteration = loader.load(as_of=datetime(2026, 8, 24, 12, 0, tzinfo=UTC))

    assert len(reader.calls) == 1
    assert (
        iteration.data_for(planned.identity)
        .get(
            DataSource.PI_INTERPOLATED,
            DataPartition.LATEST,
        )
        .last_value_number('pressure')
        == 1.0
    )


def test_iteration_rejects_loaded_sources_built_for_a_different_plan() -> None:
    session = build_alarm_execution_session(
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        planned_alarms=(),
        evaluator_registry=AlarmEvaluatorRegistry(()),
    )
    foreign_plan = DataLoadPlan(views=(), requirements_by_key={'other': ()})
    loader = RecordingLoader(lambda _plan, as_of: _empty_loaded(foreign_plan, as_of))

    with pytest.raises(AlarmExecutionIterationError, match='must match'):
        AlarmIterationLoader(session=session, source_loader=loader).load(
            as_of=datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
        )


def test_iteration_rejects_source_loader_that_changes_as_of() -> None:
    session = build_alarm_execution_session(
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        planned_alarms=(),
        evaluator_registry=AlarmEvaluatorRegistry(()),
    )
    wrong_as_of = datetime(2026, 8, 24, 12, 1, tzinfo=UTC)
    loader = RecordingLoader(lambda plan, _as_of: _empty_loaded(plan, wrong_as_of))

    with pytest.raises(AlarmExecutionIterationError, match='requested iteration as_of'):
        AlarmIterationLoader(session=session, source_loader=loader).load(
            as_of=datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
        )


def test_invalid_as_of_is_rejected_before_source_loading() -> None:
    session = build_alarm_execution_session(
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        planned_alarms=(),
        evaluator_registry=AlarmEvaluatorRegistry(()),
    )
    loader = RecordingLoader(_empty_loaded)

    with pytest.raises(ValueError, match='timezone-aware'):
        AlarmIterationLoader(session=session, source_loader=loader).load(
            as_of=datetime(2026, 8, 24, 12, 0)
        )

    assert loader.calls == []


def test_iteration_data_for_rejects_alarm_outside_the_frozen_session() -> None:
    session = _session(('risk', 'pressure'))
    loader, _reader = _real_iteration_loader(session)
    iteration = loader.load(as_of=datetime(2026, 8, 24, 12, 0, tzinfo=UTC))

    with pytest.raises(AlarmExecutionSessionError, match='not part of the execution session'):
        iteration.data_for(AlarmIdentity(family_key='mill', alarm_key='outside'))
