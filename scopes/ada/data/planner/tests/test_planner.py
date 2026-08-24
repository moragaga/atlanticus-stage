from ada.data.core import (
    DataColumn,
    DataColumnType,
    DataPartition,
    DataRequirement,
    DataSource,
    ShiftScope,
    ShiftSelection,
    TimeWindow,
    TimeWindowUnit,
)
from ada.data.planner import DataPlanSchemaError, DataRequirementPlanner


def _float(name: str) -> DataColumn:
    return DataColumn(name, DataColumnType.FLOAT)


def _text(name: str) -> DataColumn:
    return DataColumn(name, DataColumnType.TEXT)


def test_planner_merges_columns_and_selectors_per_source_partition() -> None:
    three_days = TimeWindow(3, TimeWindowUnit.DAYS)
    two_hours = TimeWindow(2, TimeWindowUnit.HOURS)
    requirements = {
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

    plan = DataRequirementPlanner().plan(requirements)

    daily = plan.view_plan(DataSource.PI_INTERPOLATED, DataPartition.DAILY)
    assert daily.column_names == ('a', 'b', 'c', 'd', 'e')
    assert daily.time_windows == (three_days, two_hours)
    exact = plan.requirements_for('b')[0]
    assert exact.column_names == ('a', 'c', 'e')
    assert exact.time_window is two_hours


def test_planner_keeps_latest_daily_and_shift_as_distinct_views() -> None:
    current = ShiftSelection(ShiftScope.CURRENT)
    days = ShiftSelection(ShiftScope.DAYS, days=3)
    requirements = {
        'latest': (
            DataRequirement(
                source=DataSource.PI_INTERPOLATED,
                partition=DataPartition.LATEST,
                columns=(_float('latest'),),
            ),
        ),
        'daily': (
            DataRequirement(
                source=DataSource.PI_INTERPOLATED,
                partition=DataPartition.DAILY,
                columns=(_float('series'),),
                time_window=TimeWindow(90, TimeWindowUnit.MINUTES),
            ),
        ),
        'shift_a': (
            DataRequirement(
                source=DataSource.DISPATCH_STD_SHIFT_STATE,
                partition=DataPartition.SHIFT,
                columns=(_text('state'),),
                shift=current,
            ),
        ),
        'shift_b': (
            DataRequirement(
                source=DataSource.DISPATCH_STD_SHIFT_STATE,
                partition=DataPartition.SHIFT,
                columns=(_text('eqmt'), _text('state')),
                shift=days,
            ),
        ),
    }

    plan = DataRequirementPlanner().plan(requirements)

    assert plan.view_plan(DataSource.PI_INTERPOLATED, DataPartition.LATEST).column_names == (
        'latest',
    )
    assert plan.view_plan(DataSource.PI_INTERPOLATED, DataPartition.DAILY).column_names == (
        'series',
    )
    dispatch = plan.view_plan(DataSource.DISPATCH_STD_SHIFT_STATE, DataPartition.SHIFT)
    assert dispatch.column_names == ('state', 'eqmt')
    assert dispatch.shifts == (current, days)
    assert plan.sources == (DataSource.PI_INTERPOLATED, DataSource.DISPATCH_STD_SHIFT_STATE)


def test_same_consumer_can_request_two_partitions_of_same_source() -> None:
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

    plan = DataRequirementPlanner().plan({'multi': (latest, daily)})

    assert plan.requirements_for('multi') == (latest, daily)
    assert tuple(view.partition for view in plan.views) == (
        DataPartition.LATEST,
        DataPartition.DAILY,
    )


def test_planner_rejects_conflicting_types_for_same_column_in_same_view() -> None:
    first = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(_float('shared'),),
    )
    second = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(_text('shared'),),
    )

    try:
        DataRequirementPlanner().plan({'a': (first,), 'b': (second,)})
    except DataPlanSchemaError as error:
        assert 'shared' in str(error)
        assert 'float != text' in str(error)
    else:
        raise AssertionError('conflicting data types must fail planning')
