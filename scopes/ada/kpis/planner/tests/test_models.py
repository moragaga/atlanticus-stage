import pytest

from ada.kpis.core import KpiSource, KpiTimeWindow, KpiTimeWindowUnit
from ada.kpis.planner import KpiLoadPlan, KpiPlanKeyError, KpiSourceLoadPlan


def test_load_plan_lookups_are_typed() -> None:
    source_plan = KpiSourceLoadPlan(
        source=KpiSource.PI_INTERPOLATED,
        time_columns=('a',),
        time_window=KpiTimeWindow(1, KpiTimeWindowUnit.HOURS),
    )
    plan = KpiLoadPlan(sources=(source_plan,), requirements_by_kpi={'a': {}})

    assert plan.source_plan(KpiSource.PI_INTERPOLATED) is source_plan
    assert plan.requirements_for('a') == {}
    with pytest.raises(KpiPlanKeyError):
        plan.source_plan(KpiSource.PI_RECORDED)
    with pytest.raises(KpiPlanKeyError):
        plan.requirements_for('missing')
