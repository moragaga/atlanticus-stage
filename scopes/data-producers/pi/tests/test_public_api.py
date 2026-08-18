import atlanticus.data_producers.pi as producer


def test_public_api_exposes_pi_producer_contracts() -> None:
    expected = {
        'PiDataProducerComponents',
        'PiDataProducerJob',
        'PiDataProducerMaterializer',
        'PiExecutionPlan',
        'PiExecutionPlanPreparer',
        'PiProducerState',
        'PiSlotPlanner',
        'PiSourceState',
        'PiStreamSetAcquirer',
        'PiWatermarkCoordinator',
        'WebIdRegistry',
        'build_pi_data_producer',
    }

    assert expected.issubset(set(producer.__all__))
