# Combina lotes del mismo modo antes de una única materialización.
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pandas as pd

from atlanticus.connectivity.service_bus import ServiceBusMessage
from atlanticus.data_producers.notpii.connector import NotPiiConnector
from atlanticus.data_producers.notpii.materialization import NotPiiMaterializer
from atlanticus.data_producers.notpii.models import NotPiiBatch, NotPiiProcessingResult
from atlanticus.integrations.pi.contracts import PiCatalog, PiExtractionMode


class NotPiiProcessor:
    def __init__(
        self,
        *,
        connector: NotPiiConnector,
        materializer: NotPiiMaterializer,
        catalog: PiCatalog,
        extraction_mode: PiExtractionMode,
    ) -> None:
        self._connector = connector
        self._materializer = materializer
        self._catalog = catalog
        self._extraction_mode = extraction_mode

    def read(self, message: ServiceBusMessage) -> NotPiiBatch:
        return self._connector.read(
            message=message,
            catalog=self._catalog,
            extraction_mode=self._extraction_mode,
        )

    def publish(self, batches: Sequence[NotPiiBatch]) -> NotPiiProcessingResult:
        resolved = tuple(batches)
        if not resolved:
            return NotPiiProcessingResult(
                message_count=0,
                row_count=0,
                materialized_row_count=0,
                publications=(),
            )
        if any(batch.extraction_mode is not self._extraction_mode for batch in resolved):
            raise ValueError('batches must match the processor extraction mode')
        raw_frame = pd.concat((batch.data for batch in resolved), ignore_index=True)
        frame = _coalesce_by_timestamp(raw_frame)
        combined = NotPiiBatch(
            message_id='iteration-batch',
            data=frame,
            extraction_mode=self._extraction_mode,
        )
        publications = self._materializer.publish(combined)
        return NotPiiProcessingResult(
            message_count=len(resolved),
            row_count=sum(len(batch.data) for batch in resolved),
            materialized_row_count=len(frame),
            publications=publications,
            source_last_updated_at_utc=_source_last_updated_at_utc(frame),
        )


def _coalesce_by_timestamp(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return data.copy()
    frame = data.copy()
    frame['timestamp_utc'] = pd.to_datetime(frame['timestamp_utc'], utc=True, errors='coerce')
    if frame['timestamp_utc'].isna().any():
        return frame
    value_columns = [column for column in frame.columns if column != 'timestamp_utc']
    if not value_columns:
        return frame.drop_duplicates(subset=['timestamp_utc'], keep='last')
    return (
        frame.groupby('timestamp_utc', as_index=False, sort=True)[value_columns]
        .last()
        .reset_index(drop=True)
    )


def _source_last_updated_at_utc(data: pd.DataFrame) -> datetime | None:
    if data.empty or 'timestamp_utc' not in data.columns:
        return None
    value = data['timestamp_utc'].max()
    converter = getattr(value, 'to_pydatetime', None)
    if callable(converter):
        value = converter()
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    return value.astimezone(UTC)
