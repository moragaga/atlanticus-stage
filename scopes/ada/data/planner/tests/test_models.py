import pytest

from ada.data.core import (
    DataColumn,
    DataColumnType,
    DataPartition,
    DataRequirement,
    DataSource,
    DataSourceView,
    TimeWindow,
    TimeWindowUnit,
)
from ada.data.planner import DataLoadPlan, DataPlanKeyError, DataSourceViewLoadPlan


def test_load_plan_lookups_are_typed_by_source_and_partition() -> None:
    view = DataSourceView(DataSource.PI_INTERPOLATED, DataPartition.DAILY)
    window = TimeWindow(1, TimeWindowUnit.HOURS)
    column = DataColumn('a', DataColumnType.FLOAT)
    source_plan = DataSourceViewLoadPlan(view=view, columns=(column,), time_windows=(window,))
    requirement = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.DAILY,
        columns=(column,),
        time_window=window,
    )
    plan = DataLoadPlan(views=(source_plan,), requirements_by_key={'a': (requirement,)})

    assert plan.view_plan(DataSource.PI_INTERPOLATED, DataPartition.DAILY) is source_plan
    assert plan.requirements_for('a') == (requirement,)
    with pytest.raises(DataPlanKeyError):
        plan.view_plan(DataSource.PI_INTERPOLATED, DataPartition.MONTHLY)
    with pytest.raises(DataPlanKeyError):
        plan.requirements_for('missing')
