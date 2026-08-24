import ada.data.planner as planner


def test_public_api() -> None:
    assert planner.__version__ == '0.1.0'
    assert planner.DataRequirementPlanner is not None
    assert planner.DataLoadPlan is not None
    assert planner.DataSourceViewLoadPlan is not None
    assert planner.DataPlanKeyError is not None
    assert planner.DataPlanSchemaError is not None
