from ada.kpis.core import KpiWatermark
from ada.kpis.delivery import KpiDeliveryConfiguration
from ada.processes.kpis_delivery import (
    KpiCommittedWatermarkReader,
    KpiDeliveryCheckpointStore,
    KpiDeliveryConfigurationReader,
    KpiLatestReader,
    KpiLatestSnapshotPublisher,
)


class ConfigurationReader:
    def read(self) -> KpiDeliveryConfiguration:
        raise NotImplementedError


class WatermarkReader:
    def read_watermark(self) -> KpiWatermark | None:
        return None


class LatestReader:
    def read(self):
        return None


class CheckpointStore:
    def read(self):
        return None

    def commit(self, checkpoint):
        return checkpoint


class SnapshotPublisher:
    def publish(self, snapshot):
        return None


def test_process_contracts_are_structural() -> None:
    assert isinstance(ConfigurationReader(), KpiDeliveryConfigurationReader)
    assert isinstance(WatermarkReader(), KpiCommittedWatermarkReader)
    assert isinstance(LatestReader(), KpiLatestReader)
    assert isinstance(CheckpointStore(), KpiDeliveryCheckpointStore)
    assert isinstance(SnapshotPublisher(), KpiLatestSnapshotPublisher)
