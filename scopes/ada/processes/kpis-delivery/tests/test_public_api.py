import ada.processes.kpis_delivery as delivery_process


def test_public_api_exports_repository_contract() -> None:
    assert set(delivery_process.__all__) == {
        'KpiDeliveryRepositoryError',
        'KpiLatestPublication',
        'KpiLatestPublicationStatus',
        'KpiLatestSnapshotRepository',
        '__version__',
    }
