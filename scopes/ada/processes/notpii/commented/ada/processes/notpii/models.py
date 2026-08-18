# Espejo comentado del proceso NOTPII: composición, batch, materialización, estado y settlement.
# Este archivo conserva exactamente el comportamiento productivo y agrega solo contexto.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from atlanticus.datasets.results import DatasetPublicationResult


@dataclass(frozen=True, slots=True)
class NotPiiProcessingResult:
    message_count: int
    row_count: int
    materialized_row_count: int
    publications: tuple[DatasetPublicationResult, ...]
    source_last_updated_at_utc: datetime | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ('message_count', self.message_count),
            ('row_count', self.row_count),
            ('materialized_row_count', self.materialized_row_count),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f'{field_name} must be a non-negative integer')
        if not all(isinstance(item, DatasetPublicationResult) for item in self.publications):
            raise ValueError('publications must contain DatasetPublicationResult values')
        if (
            self.source_last_updated_at_utc is not None
            and self.source_last_updated_at_utc.tzinfo is None
        ):
            raise ValueError('source_last_updated_at_utc must be timezone-aware')
