import ada.kpis.delivery as delivery


def test_version_and_public_contract():
    assert delivery.__version__ == '0.2.0'
    assert delivery.KPI_CONFIGURATION_ID == 'kpis'
    assert delivery.KPI_LATEST_DELIVERY_ID == 'latest'
    assert delivery.KPI_TIMESERIES_DELIVERY_ID == 'timeseries'
