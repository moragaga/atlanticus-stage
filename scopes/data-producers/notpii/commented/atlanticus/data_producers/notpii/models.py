# Modelos inmutables usados durante adquisición y procesamiento.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from atlanticus.data_producers.notpii.errors import (
    NotPiiDataProducerConfigurationError,
    NotPiiSourceError,
)
from atlanticus.datasets.results import DatasetPublicationResult
from atlanticus.integrations.pi.contracts import PiExtractionMode


@dataclass(frozen=True, slots=True)
class NotPiiBlobMessage:
    message_id: str
    url: str = field(repr=False)
    sas_token: str = field(repr=False)
    event_time_utc: datetime | None = None


@dataclass(frozen=True, slots=True)
class NotPiiBatch:
    message_id: str
    data: pd.DataFrame = field(repr=False)
    extraction_mode: PiExtractionMode

    def __post_init__(self) -> None:
        if not isinstance(self.message_id, str) or not self.message_id.strip():
            raise NotPiiDataProducerConfigurationError('message_id must be a non-empty string')
        object.__setattr__(self, 'message_id', self.message_id.strip())
        if not isinstance(self.data, pd.DataFrame):
            raise NotPiiDataProducerConfigurationError('data must be a pandas.DataFrame')
        if 'timestamp_utc' not in self.data.columns:
            raise NotPiiDataProducerConfigurationError('data must contain timestamp_utc')
        if not isinstance(self.extraction_mode, PiExtractionMode):
            raise NotPiiDataProducerConfigurationError('extraction_mode must be a PiExtractionMode')


def optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    except ValueError as error:
        raise NotPiiSourceError('NotPII eventTime must be an ISO 8601 timestamp') from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NotPiiSourceError('NotPII eventTime must include timezone information')
    return parsed.astimezone(UTC)


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


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
