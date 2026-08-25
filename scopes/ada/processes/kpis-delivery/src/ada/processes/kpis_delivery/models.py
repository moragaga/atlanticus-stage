from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ada.kpis.core import KpiWatermark
from ada.processes.kpis_delivery.errors import KpiDeliveryRepositoryError


class KpiLatestPublicationStatus(StrEnum):
    PUBLISHED = 'published'
    UNCHANGED = 'unchanged'


@dataclass(frozen=True, slots=True)
class KpiLatestPublication:
    status: KpiLatestPublicationStatus
    revision: str

    @property
    def published(self) -> bool:
        return self.status is KpiLatestPublicationStatus.PUBLISHED


@dataclass(frozen=True, slots=True)
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
