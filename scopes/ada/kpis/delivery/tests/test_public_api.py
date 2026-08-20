import ada.kpis.delivery as delivery


def test_public_api_exports_latest_delivery_contract() -> None:
    expected = {
        'KPI_LATEST_DELIVERY_ID',
        'KPI_LATEST_PARTITION_ID',
        'KPI_LATEST_SCHEMA_VERSION',
        'KpiDeliveryBinding',
        'KpiDeliveryError',
        'KpiDeliveryManifest',
        'KpiDeliverySnapshot',
        'KpiDeliveryStatus',
        'KpiDeliveryValidationError',
        'KpiDeliveryValue',
        'calculate_kpi_latest_revision',
        'project_kpi_latest',
    }

    assert expected <= set(delivery.__all__)
