# Proceso Series: lee Historian, selecciona timestamps exactos y publica una proyección compacta.
# Contiene modelos inmutables y validaciones del contrato.

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ada.kpis.core import KpiWatermark
from ada.processes.kpis_timeseries_delivery.errors import KpiTimeseriesDeliveryRepositoryError


# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesPublicationStatus(StrEnum):
    PUBLISHED = 'published'
    UNCHANGED = 'unchanged'


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesPublication:
    status: KpiTimeseriesPublicationStatus
    revision: str
    document_count: int


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesCheckpoint:
    watermark: KpiWatermark | None
    configuration_revision: str

    def __post_init__(self) -> None:
        if self.watermark is not None and not isinstance(self.watermark, KpiWatermark):
            raise KpiTimeseriesDeliveryRepositoryError(
                'checkpoint watermark must be KpiWatermark or None'
            )
        if not isinstance(self.configuration_revision, str) or not self.configuration_revision:
            raise KpiTimeseriesDeliveryRepositoryError(
                'checkpoint configuration_revision must be a non-empty string'
            )
        if self.configuration_revision != self.configuration_revision.strip():
            raise KpiTimeseriesDeliveryRepositoryError(
                'checkpoint configuration_revision must not contain surrounding whitespace'
            )
