# Proceso Latest: congela configuración por job, observa watermark fresco y publica sólo cuando corresponde.
# Contiene modelos inmutables y validaciones del contrato.

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ada.kpis.core import KpiWatermark
from ada.processes.kpis_delivery.errors import KpiDeliveryRepositoryError


# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiLatestPublicationStatus(StrEnum):
    PUBLISHED = 'published'
    UNCHANGED = 'unchanged'


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiLatestPublication:
    status: KpiLatestPublicationStatus
    revision: str

    @property
    def published(self) -> bool:
        return self.status is KpiLatestPublicationStatus.PUBLISHED


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiDeliveryCheckpoint:
    watermark: KpiWatermark | None
    configuration_revision: str

    def __post_init__(self) -> None:
        if self.watermark is not None and not isinstance(self.watermark, KpiWatermark):
            raise KpiDeliveryRepositoryError('checkpoint watermark must be KpiWatermark or None')
        if not isinstance(self.configuration_revision, str) or not self.configuration_revision:
            raise KpiDeliveryRepositoryError(
                'checkpoint configuration_revision must be a non-empty string'
            )
        if self.configuration_revision != self.configuration_revision.strip():
            raise KpiDeliveryRepositoryError(
                'checkpoint configuration_revision must not contain surrounding whitespace'
            )
