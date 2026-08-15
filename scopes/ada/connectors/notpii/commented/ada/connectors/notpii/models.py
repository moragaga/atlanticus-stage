# Espejo comentado del conector NOTPII: adquisición Service Bus/Storage sin lógica adicional.
# Este archivo conserva exactamente el comportamiento productivo y agrega solo contexto.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from ada.connectors.notpii.errors import NotPiiConfigurationError, NotPiiSourceError
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
            raise NotPiiConfigurationError('message_id must be a non-empty string')
        object.__setattr__(self, 'message_id', self.message_id.strip())
        if not isinstance(self.data, pd.DataFrame):
            raise NotPiiConfigurationError('data must be a pandas.DataFrame')
        if 'timestamp_utc' not in self.data.columns:
            raise NotPiiConfigurationError('data must contain timestamp_utc')
        if not isinstance(self.extraction_mode, PiExtractionMode):
            raise NotPiiConfigurationError('extraction_mode must be a PiExtractionMode')


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
