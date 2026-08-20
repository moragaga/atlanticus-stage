from __future__ import annotations

from typing import Protocol, runtime_checkable

from ada.kpis.core import KpiEvaluation
from ada.kpis.delivery import KpiDeliveryBinding, KpiDeliverySnapshot
from ada.processes.kpis_delivery.models import KpiLatestPublication


@runtime_checkable
class KpiLatestReader(Protocol):
    def read(self) -> KpiEvaluation | None: ...


@runtime_checkable
class KpiDeliveryBindingsReader(Protocol):
    def read_bindings(self) -> tuple[KpiDeliveryBinding, ...]: ...


@runtime_checkable
class KpiLatestSnapshotPublisher(Protocol):
    def publish(self, snapshot: KpiDeliverySnapshot) -> KpiLatestPublication: ...
