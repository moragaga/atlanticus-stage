from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from ada.kpis.core import KpiWatermark
from ada.kpis.delivery import KpiDeliveryConfiguration, KpiTimeseriesPoint, KpiTimeseriesSnapshot
from ada.processes.kpis_timeseries_delivery.models import (
    KpiTimeseriesCheckpoint,
    KpiTimeseriesPublication,
)


@runtime_checkable
class KpiTimeseriesConfigurationReader(Protocol):
    def read(self) -> KpiDeliveryConfiguration: ...


@runtime_checkable
class KpiHistorianWatermarkReader(Protocol):
    def read_watermark(self) -> KpiWatermark | None: ...


@runtime_checkable
class KpiTimeseriesHistoryReader(Protocol):
    def read_points(
        self,
        *,
        keys: tuple[str, ...],
        start_utc: datetime,
        end_utc: datetime,
        step_seconds: int,
    ) -> tuple[KpiTimeseriesPoint, ...]: ...


@runtime_checkable
class KpiTimeseriesCheckpointStore(Protocol):
    def read(self) -> KpiTimeseriesCheckpoint | None: ...

    def commit(self, checkpoint: KpiTimeseriesCheckpoint) -> KpiTimeseriesCheckpoint: ...


@runtime_checkable
class KpiTimeseriesSnapshotPublisher(Protocol):
    def publish(self, snapshot: KpiTimeseriesSnapshot) -> KpiTimeseriesPublication: ...
