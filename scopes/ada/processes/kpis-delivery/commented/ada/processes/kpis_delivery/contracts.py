# Puertos estructurales del job: conectan persistencia y configuración sin acoplar la orquestación a su forma física.
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ada.kpis.core import KpiEvaluation
from ada.kpis.delivery import KpiDeliveryBinding, KpiDeliverySnapshot
from ada.processes.kpis_delivery.models import KpiLatestPublication


# Cualquier repositorio que entregue la evaluación latest puede ocupar este puerto, incluido KpiLatestRepository.
@runtime_checkable
class KpiLatestReader(Protocol):
    def read(self) -> KpiEvaluation | None: ...


# El adapter futuro de configuración Cosmos solo debe normalizar su snapshot a bindings de Delivery.
@runtime_checkable
class KpiDeliveryBindingsReader(Protocol):
    def read_bindings(self) -> tuple[KpiDeliveryBinding, ...]: ...


# La publicación se expresa como puerto para que el job no dependa de Cosmos; el repository actual satisface este contrato.
@runtime_checkable
class KpiLatestSnapshotPublisher(Protocol):
    def publish(self, snapshot: KpiDeliverySnapshot) -> KpiLatestPublication: ...
