import ada.processes.kpis_timeseries_delivery as module


def test_version():
    assert module.__version__ == '0.1.0'
    assert module.KPI_TIMESERIES_STEP_SECONDS == 120
