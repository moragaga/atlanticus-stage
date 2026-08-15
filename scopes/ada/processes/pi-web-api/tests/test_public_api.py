import ada.processes.pi_web_api as process


def test_public_api_exposes_preparation_planning_and_state_contracts() -> None:
    expected = {
        'PiExecutionPlan',
        'PiExecutionPlanPreparer',
        'PiWebApiJob',
        'PiSlotPlanner',
        'PiProducerState',
        'PiSourceState',
        'PiWatermarkCoordinator',
        'WebIdRegistry',
        'build_composition',
        'load_configuration',
    }

    assert expected.issubset(set(process.__all__))
