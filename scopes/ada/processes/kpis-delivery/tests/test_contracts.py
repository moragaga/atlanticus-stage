from ada.kpis.delivery import KpiDeliveryBinding
from ada.processes.kpis_delivery import (
    KpiDeliveryBindingsReader,
    KpiLatestReader,
    KpiLatestSnapshotPublisher,
)


class LatestReader:
    def read(self):
        return None


class BindingsReader:
    def read_bindings(self):
        return (KpiDeliveryBinding(store_key='chancado', kpi_key='tonelaje'),)


def test_latest_reader_is_structural_contract() -> None:
    assert isinstance(LatestReader(), KpiLatestReader)


def test_bindings_reader_is_structural_contract() -> None:
    assert isinstance(BindingsReader(), KpiDeliveryBindingsReader)


class SnapshotPublisher:
    def publish(self, snapshot):
        return None


def test_snapshot_publisher_is_structural_contract() -> None:
    assert isinstance(SnapshotPublisher(), KpiLatestSnapshotPublisher)
