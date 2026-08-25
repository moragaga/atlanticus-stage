# Proceso Series: lee Historian, selecciona timestamps exactos y publica una proyección compacta.
# Define los puertos mínimos que consume el job.

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
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesConfigurationReader(Protocol):
    def read(self) -> KpiDeliveryConfiguration: ...


@runtime_checkable
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiHistorianWatermarkReader(Protocol):
    def read_watermark(self) -> KpiWatermark | None: ...


@runtime_checkable
# La clase encapsula una responsabilidad con estado o contrato propio.
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
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesCheckpointStore(Protocol):
    def read(self) -> KpiTimeseriesCheckpoint | None: ...

    def commit(self, checkpoint: KpiTimeseriesCheckpoint) -> KpiTimeseriesCheckpoint: ...


@runtime_checkable
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesSnapshotPublisher(Protocol):
    def publish(self, snapshot: KpiTimeseriesSnapshot) -> KpiTimeseriesPublication: ...
