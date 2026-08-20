# Espejo comentado: el código ejecutable conserva exactamente el contrato productivo.
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ada.kpis.delivery import (
    KPI_LATEST_DELIVERY_ID,
    KPI_LATEST_PARTITION_ID,
    KpiDeliverySnapshot,
)
from ada.processes.kpis_delivery.errors import KpiDeliveryRepositoryError
from ada.processes.kpis_delivery.models import (
    KpiLatestPublication,
    KpiLatestPublicationStatus,
)
from atlanticus.connectivity.cosmos import CosmosClient


@dataclass(slots=True)
class KpiLatestSnapshotRepository:
    client: CosmosClient
    container_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.container_name, str) or not self.container_name:
            raise KpiDeliveryRepositoryError('container_name must be a non-empty string')
        if self.container_name != self.container_name.strip():
            raise KpiDeliveryRepositoryError(
                'container_name must not contain surrounding whitespace'
            )

    def publish(self, snapshot: KpiDeliverySnapshot) -> KpiLatestPublication:
        self._validate_snapshot(snapshot)
        current = self.client.find_item(
            container_name=self.container_name,
            item_id=snapshot.id,
            partition_key=snapshot.partition_id,
        )
        if current is not None:
            current_revision = self._current_revision(current)
            if current_revision == snapshot.manifest.revision:
                return KpiLatestPublication(
                    status=KpiLatestPublicationStatus.UNCHANGED,
                    revision=snapshot.manifest.revision,
                )
        self.client.upsert_item(
            container_name=self.container_name,
            item=snapshot.as_document(),
        )
        return KpiLatestPublication(
            status=KpiLatestPublicationStatus.PUBLISHED,
            revision=snapshot.manifest.revision,
        )

    @staticmethod
    def _validate_snapshot(snapshot: KpiDeliverySnapshot) -> None:
        if not isinstance(snapshot, KpiDeliverySnapshot):
            raise TypeError('snapshot must be KpiDeliverySnapshot')
        if snapshot.id != KPI_LATEST_DELIVERY_ID:
            raise KpiDeliveryRepositoryError('snapshot id is not valid for KPI latest delivery')
        if snapshot.partition_id != KPI_LATEST_PARTITION_ID:
            raise KpiDeliveryRepositoryError(
                'snapshot partition_id is not valid for KPI latest delivery'
            )

    @staticmethod
    def _current_revision(document: dict[str, Any]) -> str:
        manifest = document.get('manifest')
        if not isinstance(manifest, dict):
            raise KpiDeliveryRepositoryError('existing KPI latest snapshot manifest is invalid')
        revision = manifest.get('revision')
        if not isinstance(revision, str) or not revision or revision != revision.strip():
            raise KpiDeliveryRepositoryError('existing KPI latest snapshot revision is invalid')
        return revision
