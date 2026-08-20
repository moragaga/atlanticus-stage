import pytest

from ada.kpis.core import (
    KpiPartition,
    KpiSource,
    KpiSourceView,
    KpiTimeWindow,
    KpiTimeWindowUnit,
    SourceRequirement,
)
from ada.kpis.planner import KpiLoadPlan, KpiPlanKeyError, KpiSourceViewLoadPlan


def test_load_plan_lookups_are_typed_by_source_and_partition() -> None:
    view = KpiSourceView(KpiSource.PI_INTERPOLATED, KpiPartition.DAILY)
    window = KpiTimeWindow(1, KpiTimeWindowUnit.HOURS)
    source_plan = KpiSourceViewLoadPlan(
        view=view,
        columns=('a',),
        time_windows=(window,),
    )
    requirement = SourceRequirement(
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.DAILY,
        columns=('a',),
        time_window=window,
    )
    plan = KpiLoadPlan(
        views=(source_plan,),
        requirements_by_kpi={'a': (requirement,)},
    )

    assert plan.view_plan(KpiSource.PI_INTERPOLATED, KpiPartition.DAILY) is source_plan
    assert plan.requirements_for('a') == (requirement,)
    with pytest.raises(KpiPlanKeyError):
        plan.view_plan(KpiSource.PI_INTERPOLATED, KpiPartition.MONTHLY)
    with pytest.raises(KpiPlanKeyError):
        plan.requirements_for('missing')
