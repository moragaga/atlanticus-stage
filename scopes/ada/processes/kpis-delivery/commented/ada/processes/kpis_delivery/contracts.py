# Proceso Latest: congela configuración por job, observa watermark fresco y publica sólo cuando corresponde.
# Define los puertos mínimos que consume el job.

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ada.kpis.core import KpiEvaluation, KpiWatermark
from ada.kpis.delivery import KpiDeliveryConfiguration, KpiDeliverySnapshot
from ada.processes.kpis_delivery.models import KpiDeliveryCheckpoint, KpiLatestPublication


@runtime_checkable
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiDeliveryConfigurationReader(Protocol):
    def read(self) -> KpiDeliveryConfiguration: ...


@runtime_checkable
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiCommittedWatermarkReader(Protocol):
    def read_watermark(self) -> KpiWatermark | None: ...


@runtime_checkable
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiLatestReader(Protocol):
    def read(self) -> KpiEvaluation | None: ...


@runtime_checkable
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiDeliveryCheckpointStore(Protocol):
    def read(self) -> KpiDeliveryCheckpoint | None: ...

    def commit(self, checkpoint: KpiDeliveryCheckpoint) -> KpiDeliveryCheckpoint: ...


@runtime_checkable
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiLatestSnapshotPublisher(Protocol):
    def publish(self, snapshot: KpiDeliverySnapshot) -> KpiLatestPublication: ...
