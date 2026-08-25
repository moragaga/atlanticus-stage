import ada.processes.kpis_delivery as module


def test_version():
    assert module.__version__ == '0.2.0'
    assert module.KPI_CONFIGURATION_CONTAINER_NAME == 'configuration'
    assert module.KPI_LATEST_DELIVERY_CONTAINER_NAME == 'kpis-latest-delivery'
