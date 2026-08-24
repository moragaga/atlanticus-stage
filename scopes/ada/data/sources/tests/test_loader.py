from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import pandas as pd
import pyarrow as pa
import pytest

from ada.data.core import (
    DataColumn,
    DataColumnType,
    DataPartition,
    DataRequirement,
    DataSource,
    DataSourceNotRequestedError,
    ShiftScope,
    ShiftSelection,
    TimeWindow,
    TimeWindowUnit,
)
from ada.data.planner import DataRequirementPlanner
from ada.data.sources import (
    DataPartitionBinding,
    DataSourceBinding,
    DataSourceLoader,
    DataSourceRegistry,
    DataSourceSchemaError,
    DataSourceUnavailableError,
    TimePartitionGranularity,
)
from atlanticus.datasets.layouts import SingleArtifactLayout
from atlanticus.datasets.models import (
    DatasetDefinition,
    DatasetKey,
    DatasetTarget,
    MaterializationDefinition,
)


class FakeReader:
    def __init__(
        self,
        frames: Mapping[str, pd.DataFrame],
        failures: Mapping[str, Exception] | None = None,
    ) -> None:
        self.frames = dict(frames)
        self.failures = {} if failures is None else dict(failures)
        self.calls: list[tuple[str, pa.Schema, datetime | None, datetime | None]] = []

    def read_frame(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        projection_schema: pa.Schema,
        timestamp_column: str | None = None,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
    ) -> pd.DataFrame | None:
        identifier = target.identifier
        self.calls.append((identifier, projection_schema, start_utc, end_utc))
        failure = self.failures.get(identifier)
        if failure is not None:
            raise failure
        source = self.frames.get(identifier)
        if source is None:
            return None
        frame = pd.DataFrame(index=source.index)
        for field in projection_schema:
            if field.name in source.columns:
                frame[field.name] = source[field.name]
            else:
                frame[field.name] = None
        if timestamp_column is not None and start_utc is not None and end_utc is not None:
            timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors='coerce')
            frame = frame.loc[
                (timestamps >= pd.Timestamp(start_utc)) & (timestamps <= pd.Timestamp(end_utc))
            ]
        return frame.reset_index(drop=True)


def _float(name: str) -> DataColumn:
    return DataColumn(name, DataColumnType.FLOAT)


def _text(name: str) -> DataColumn:
    return DataColumn(name, DataColumnType.TEXT)


def _pi_binding() -> DataSourceBinding:
    definition = DatasetDefinition(
        key=DatasetKey(namespace=('pi', 'web-api'), name='interpolated'),
        materializations=(
            MaterializationDefinition(name='latest', layout=SingleArtifactLayout()),
            MaterializationDefinition(
                name='daily',
                layout=SingleArtifactLayout(),
                partition_dimensions=('year', 'month', 'day'),
            ),
            MaterializationDefinition(
                name='monthly',
                layout=SingleArtifactLayout(),
                partition_dimensions=('year', 'month'),
            ),
        ),
    )
    return DataSourceBinding(
        source=DataSource.PI_INTERPOLATED,
        definition=definition,
        partitions={
            DataPartition.LATEST: DataPartitionBinding(
                partition=DataPartition.LATEST,
                materialization='latest',
                timestamp_column='timestamp_utc',
            ),
            DataPartition.DAILY: DataPartitionBinding(
                partition=DataPartition.DAILY,
                materialization='daily',
                time_partition_granularity=TimePartitionGranularity.DAY,
                timestamp_column='timestamp_utc',
            ),
            DataPartition.MONTHLY: DataPartitionBinding(
                partition=DataPartition.MONTHLY,
                materialization='monthly',
                time_partition_granularity=TimePartitionGranularity.MONTH,
                timestamp_column='timestamp_utc',
            ),
        },
    )


def _shift_binding() -> DataSourceBinding:
    definition = DatasetDefinition(
        key=DatasetKey(namespace=('dispatch',), name='std_shift_state'),
        materializations=(
            MaterializationDefinition(
                name='shift',
                layout=SingleArtifactLayout(),
                partition_dimensions=('year', 'month', 'day', 'turn'),
            ),
        ),
    )
    return DataSourceBinding(
        source=DataSource.DISPATCH_STD_SHIFT_STATE,
        definition=definition,
        partitions={
            DataPartition.SHIFT: DataPartitionBinding(
                partition=DataPartition.SHIFT,
                materialization='shift',
                shift_column='shift_id',
            )
        },
    )


def _plan(requirements_by_key: Mapping[str, tuple[DataRequirement, ...]]):
    return DataRequirementPlanner().plan(requirements_by_key)


def test_loader_overfetches_daily_once_but_each_context_gets_exact_window_and_columns() -> None:
    three_days = TimeWindow(3, TimeWindowUnit.DAYS)
    two_hours = TimeWindow(2, TimeWindowUnit.HOURS)
    plan = _plan(
        {
            'a': (
                DataRequirement(
                    source=DataSource.PI_INTERPOLATED,
                    partition=DataPartition.DAILY,
                    columns=tuple(_float(name) for name in ('a', 'b', 'c', 'd', 'e')),
                    time_window=three_days,
                ),
            ),
            'b': (
                DataRequirement(
                    source=DataSource.PI_INTERPOLATED,
                    partition=DataPartition.DAILY,
                    columns=tuple(_float(name) for name in ('a', 'c', 'e')),
                    time_window=two_hours,
                ),
            ),
        }
    )
    binding = _pi_binding()
    frames = {}
    for day in (16, 17, 18, 19):
        target = binding.definition.resolve_target(
            materialization='daily',
            partition={'year': '2026', 'month': '08', 'day': f'{day:02d}'},
        )
        frames[target.identifier] = pd.DataFrame(
            {
                'timestamp_utc': [datetime(2026, 8, day, 10, tzinfo=UTC)],
                'a': [day],
                'b': [day + 1],
                'c': [day + 2],
                'd': [day + 3],
                'e': [day + 4],
            }
        )
    final_target = binding.definition.resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '19'},
    )
    frames[final_target.identifier] = pd.concat(
        (
            frames[final_target.identifier],
            pd.DataFrame(
                {
                    'timestamp_utc': [datetime(2026, 8, 19, 11, 30, tzinfo=UTC)],
                    'a': [100],
                    'b': [101],
                    'c': [102],
                    'd': [103],
                    'e': [104],
                }
            ),
        ),
        ignore_index=True,
    )
    reader = FakeReader(frames)
    loader = DataSourceLoader(
        reader=reader,
        registry=DataSourceRegistry({DataSource.PI_INTERPOLATED: binding}),
    )
    as_of = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)

    loaded = loader.load(plan=plan, as_of=as_of)
    pi = loaded.context_for('b').get(DataSource.PI_INTERPOLATED, DataPartition.DAILY)

    assert list(pi.dataframe.columns) == ['a', 'c', 'e']
    assert len(pi.dataframe) == 2
    assert pi.last_row().to_dict() == {'a': 100, 'c': 102, 'e': 104}
    assert all(call[1].names == ['a', 'b', 'c', 'd', 'e', 'timestamp_utc'] for call in reader.calls)
    assert all(call[1].field('a').type == pa.float64() for call in reader.calls)
    assert all(
        call[1].field('timestamp_utc').type == pa.timestamp('us', tz='UTC') for call in reader.calls
    )


def test_same_source_latest_and_daily_are_loaded_as_independent_views() -> None:
    latest = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(_float('current'),),
    )
    daily = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.DAILY,
        columns=(_float('series'),),
        time_window=TimeWindow(2, TimeWindowUnit.HOURS),
    )
    plan = _plan({'multi': (latest, daily)})
    binding = _pi_binding()
    latest_target = binding.definition.resolve_target(materialization='latest')
    daily_target = binding.definition.resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '19'},
    )
    reader = FakeReader(
        {
            latest_target.identifier: pd.DataFrame(
                {'timestamp_utc': ['2026-08-19T12:00:00Z'], 'current': [9]}
            ),
            daily_target.identifier: pd.DataFrame(
                {'timestamp_utc': ['2026-08-19T11:00:00Z'], 'series': [7]}
            ),
        }
    )
    loaded = DataSourceLoader(
        reader=reader,
        registry=DataSourceRegistry({DataSource.PI_INTERPOLATED: binding}),
    ).load(plan=plan, as_of=datetime(2026, 8, 19, 12, tzinfo=UTC))

    context = loaded.context_for('multi')
    assert context.get(DataSource.PI_INTERPOLATED, DataPartition.LATEST).last_value('current') == 9
    assert context.get(DataSource.PI_INTERPOLATED, DataPartition.DAILY).last_value('series') == 7


def test_monthly_month_window_reads_calendar_month_partitions() -> None:
    plan = _plan(
        {
            'monthly': (
                DataRequirement(
                    source=DataSource.PI_INTERPOLATED,
                    partition=DataPartition.MONTHLY,
                    columns=(_float('value'),),
                    time_window=TimeWindow(2, TimeWindowUnit.MONTHS),
                ),
            )
        }
    )
    binding = _pi_binding()
    frames = {}
    for month in (6, 7, 8):
        target = binding.definition.resolve_target(
            materialization='monthly',
            partition={'year': '2026', 'month': f'{month:02d}'},
        )
        frames[target.identifier] = pd.DataFrame(
            {'timestamp_utc': [datetime(2026, month, 15, tzinfo=UTC)], 'value': [month]}
        )
    reader = FakeReader(frames)

    loaded = DataSourceLoader(
        reader=reader,
        registry=DataSourceRegistry({DataSource.PI_INTERPOLATED: binding}),
    ).load(plan=plan, as_of=datetime(2026, 8, 31, 12, tzinfo=UTC))

    frame = loaded.context_for('monthly').get(DataSource.PI_INTERPOLATED, DataPartition.MONTHLY)
    assert frame.dataframe['value'].tolist() == [7, 8]
    assert len(reader.calls) == 3


def test_missing_physical_partitions_become_empty_exact_context() -> None:
    plan = _plan(
        {
            'empty': (
                DataRequirement(
                    source=DataSource.PI_INTERPOLATED,
                    partition=DataPartition.DAILY,
                    columns=(_float('a'), _text('b')),
                    time_window=TimeWindow(2, TimeWindowUnit.HOURS),
                ),
            )
        }
    )
    binding = _pi_binding()
    loader = DataSourceLoader(
        reader=FakeReader({}),
        registry=DataSourceRegistry({DataSource.PI_INTERPOLATED: binding}),
    )

    loaded = loader.load(plan=plan, as_of=datetime(2026, 8, 19, 12, 0, tzinfo=UTC))
    frame = loaded.context_for('empty').get(DataSource.PI_INTERPOLATED, DataPartition.DAILY)

    assert frame.dataframe.empty
    assert list(frame.dataframe.columns) == ['a', 'b']


def test_source_failure_is_isolated_until_dependent_consumer_requests_view() -> None:
    pi = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(_float('a'),),
    )
    dispatch = DataRequirement(
        source=DataSource.DISPATCH_STD_SHIFT_STATE,
        partition=DataPartition.SHIFT,
        columns=(_text('state'),),
        shift=ShiftSelection(ShiftScope.CURRENT),
    )
    plan = _plan({'pi': (pi,), 'dispatch': (dispatch,)})
    pi_binding = _pi_binding()
    shift_binding = _shift_binding()
    latest_target = pi_binding.definition.resolve_target(materialization='latest')
    current_target = shift_binding.definition.resolve_target(
        materialization='shift',
        partition={'year': '2026', 'month': '08', 'day': '20', 'turn': '001'},
    )
    reader = FakeReader(
        {
            latest_target.identifier: pd.DataFrame(
                {'timestamp_utc': ['2026-08-20T02:00:00Z'], 'a': [5]}
            )
        },
        failures={current_target.identifier: DataSourceSchemaError('bad dispatch schema')},
    )
    loader = DataSourceLoader(
        reader=reader,
        registry=DataSourceRegistry(
            {
                DataSource.PI_INTERPOLATED: pi_binding,
                DataSource.DISPATCH_STD_SHIFT_STATE: shift_binding,
            }
        ),
    )

    loaded = loader.load(plan=plan, as_of=datetime(2026, 8, 20, 2, 0, tzinfo=UTC))

    assert (
        loaded.context_for('pi')
        .get(DataSource.PI_INTERPOLATED, DataPartition.LATEST)
        .last_value_number('a')
        == 5.0
    )
    with pytest.raises(DataSourceUnavailableError):
        loaded.context_for('dispatch')
    with pytest.raises(DataSourceNotRequestedError):
        loaded.context_for('pi').get(DataSource.PI_INTERPOLATED, DataPartition.DAILY)


def test_loader_projects_missing_business_columns_as_typed_nulls_without_failing_view() -> None:
    requirement = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(_float('existing'), _text('new_text'), _float('new_number')),
    )
    plan = _plan({'kpi': (requirement,)})
    binding = _pi_binding()
    target = binding.definition.resolve_target(materialization='latest')
    reader = FakeReader(
        {
            target.identifier: pd.DataFrame(
                {
                    'timestamp_utc': ['2026-08-20T02:00:00Z'],
                    'existing': [10.0],
                }
            )
        }
    )

    loaded = DataSourceLoader(
        reader=reader,
        registry=DataSourceRegistry({DataSource.PI_INTERPOLATED: binding}),
    ).load(plan=plan, as_of=datetime(2026, 8, 20, 2, 0, tzinfo=UTC))

    frame = loaded.context_for('kpi').get(DataSource.PI_INTERPOLATED, DataPartition.LATEST)
    assert frame.last_value_number('existing') == 10.0
    assert frame.last_value('new_text') is None
    assert frame.last_value_number('new_number') is None
    schema = reader.calls[0][1]
    assert schema.field('new_text').type == pa.string()
    assert schema.field('new_number').type == pa.float64()


def test_loader_maps_all_shared_column_types_to_canonical_arrow_types() -> None:
    requirement = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(
            DataColumn('text_value', DataColumnType.TEXT),
            DataColumn('integer_value', DataColumnType.INTEGER),
            DataColumn('float_value', DataColumnType.FLOAT),
            DataColumn('boolean_value', DataColumnType.BOOLEAN),
            DataColumn('date_value', DataColumnType.DATE),
            DataColumn('datetime_value', DataColumnType.DATETIME),
        ),
    )
    plan = _plan({'consumer': (requirement,)})
    binding = _pi_binding()
    target = binding.definition.resolve_target(materialization='latest')
    reader = FakeReader(
        {target.identifier: pd.DataFrame({'timestamp_utc': ['2026-08-20T02:00:00Z']})}
    )

    DataSourceLoader(
        reader=reader,
        registry=DataSourceRegistry({DataSource.PI_INTERPOLATED: binding}),
    ).load(plan=plan, as_of=datetime(2026, 8, 20, 2, 0, tzinfo=UTC))

    schema = reader.calls[0][1]
    assert schema.field('text_value').type == pa.string()
    assert schema.field('integer_value').type == pa.int64()
    assert schema.field('float_value').type == pa.float64()
    assert schema.field('boolean_value').type == pa.bool_()
    assert schema.field('date_value').type == pa.date32()
    assert schema.field('datetime_value').type == pa.timestamp('us', tz='UTC')
    assert schema.field('timestamp_utc').type == pa.timestamp('us', tz='UTC')


def test_missing_timestamp_is_not_tolerated_as_business_data() -> None:
    requirement = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(_float('value'),),
    )
    plan = _plan({'kpi': (requirement,)})
    binding = _pi_binding()
    target = binding.definition.resolve_target(materialization='latest')
    reader = FakeReader({target.identifier: pd.DataFrame({'value': [10.0]})})

    loaded = DataSourceLoader(
        reader=reader,
        registry=DataSourceRegistry({DataSource.PI_INTERPOLATED: binding}),
    ).load(plan=plan, as_of=datetime(2026, 8, 20, 2, 0, tzinfo=UTC))

    with pytest.raises(
        DataSourceUnavailableError,
        match='timestamp column contains invalid values',
    ):
        loaded.context_for('kpi')


def test_missing_shift_id_is_not_tolerated_as_business_data() -> None:
    requirement = DataRequirement(
        source=DataSource.DISPATCH_STD_SHIFT_STATE,
        partition=DataPartition.SHIFT,
        columns=(_text('state'),),
        shift=ShiftSelection(ShiftScope.CURRENT),
    )
    plan = _plan({'alarm': (requirement,)})
    binding = _shift_binding()
    target = binding.definition.resolve_target(
        materialization='shift',
        partition={'year': '2026', 'month': '08', 'day': '20', 'turn': '001'},
    )
    reader = FakeReader({target.identifier: pd.DataFrame({'state': ['RUNNING']})})

    loaded = DataSourceLoader(
        reader=reader,
        registry=DataSourceRegistry({DataSource.DISPATCH_STD_SHIFT_STATE: binding}),
    ).load(plan=plan, as_of=datetime(2026, 8, 20, 2, 0, tzinfo=UTC))

    with pytest.raises(DataSourceUnavailableError, match='shift column contains invalid values'):
        loaded.context_for('alarm')
