from __future__ import annotations

from typing import Protocol, runtime_checkable

from ada.kpis.core import KpiEvaluation, KpiWatermark
from ada.kpis.delivery import KpiDeliveryConfiguration, KpiDeliverySnapshot
from ada.processes.kpis_delivery.models import KpiDeliveryCheckpoint, KpiLatestPublication


@runtime_checkable
class KpiDeliveryConfigurationReader(Protocol):
    def read(self) -> KpiDeliveryConfiguration: ...


@runtime_checkable
class KpiCommittedWatermarkReader(Protocol):
    def read_watermark(self) -> KpiWatermark | None: ...


@runtime_checkable
class KpiLatestReader(Protocol):
    def read(self) -> KpiEvaluation | None: ...


@runtime_checkable
class KpiDeliveryCheckpointStore(Protocol):
    def read(self) -> KpiDeliveryCheckpoint | None: ...

    def commit(self, checkpoint: KpiDeliveryCheckpoint) -> KpiDeliveryCheckpoint: ...


@runtime_checkable
class KpiLatestSnapshotPublisher(Protocol):
    def publish(self, snapshot: KpiDeliverySnapshot) -> KpiLatestPublication: ...
