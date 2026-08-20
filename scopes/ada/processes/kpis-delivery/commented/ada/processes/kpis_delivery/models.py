# Espejo comentado: frontera Cosmos del proceso KPI Latest Delivery.
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
