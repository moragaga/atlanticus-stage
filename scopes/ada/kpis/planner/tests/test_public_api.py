import ada.kpis.planner as planner


def test_public_api() -> None:
    assert planner.__version__ == '0.1.0'
    assert planner.KpiRequirementPlanner is not None
    assert planner.KpiLoadPlan is not None
    assert planner.KpiSourceLoadPlan is not None
