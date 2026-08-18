from __future__ import annotations

import json
from tempfile import TemporaryFile

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from atlanticus.connectivity.service_bus import ServiceBusMessage
from atlanticus.connectivity.storage import StorageSasReader, StorageSasReference
from atlanticus.data_producers.notpii._wide import build_wide
from atlanticus.data_producers.notpii.errors import (
    NotPiiDataProducerConfigurationError,
    NotPiiSourceError,
)
from atlanticus.data_producers.notpii.models import (
    NotPiiBatch,
    NotPiiBlobMessage,
    optional_datetime,
    optional_text,
)
from atlanticus.integrations.pi.contracts import (
    NotPiiSource,
    PiCatalog,
    PiExtractionMode,
)

_REQUIRED_COLUMNS = ('timestamp', 'id_tag', 'valor')
_OPTIONAL_COLUMNS = ('timestamp_ingesta', 'particion')


class NotPiiConnector:
    def __init__(self, *, storage_reader: StorageSasReader, raw_batch_size: int = 100_000) -> None:
        if not isinstance(storage_reader, StorageSasReader):
            raise NotPiiDataProducerConfigurationError('storage_reader must be a StorageSasReader')
        if (
            not isinstance(raw_batch_size, int)
            or isinstance(raw_batch_size, bool)
            or raw_batch_size <= 0
        ):
            raise NotPiiDataProducerConfigurationError('raw_batch_size must be greater than zero')
        self._storage_reader = storage_reader
        self.raw_batch_size = raw_batch_size

    def read(
        self,
        *,
        message: ServiceBusMessage,
        catalog: PiCatalog,
        extraction_mode: PiExtractionMode,
    ) -> NotPiiBatch:
        if not isinstance(message, ServiceBusMessage):
            raise NotPiiDataProducerConfigurationError('message must be a ServiceBusMessage')
        if not isinstance(catalog, PiCatalog):
            raise NotPiiDataProducerConfigurationError('catalog must be a PiCatalog')
        if not isinstance(catalog.source, NotPiiSource):
            raise NotPiiDataProducerConfigurationError('catalog source must be NotPiiSource')
        if not isinstance(extraction_mode, PiExtractionMode):
            raise NotPiiDataProducerConfigurationError('extraction_mode must be a PiExtractionMode')
        selected = tuple(
            item
            for item in catalog.definitions
            if item.is_active and item.extraction_mode is extraction_mode
        )
        if not selected:
            raise NotPiiDataProducerConfigurationError(
                'catalog does not contain the requested extraction mode'
            )

        blob_message = decode_message(message)
        aliases = {item.tag_name.upper(): item.alias for item in selected}
        raw_frames: list[pd.DataFrame] = []
        reference = StorageSasReference.from_values(
            sas_url=blob_message.url,
            sas_token=blob_message.sas_token,
        )
        with TemporaryFile() as stream:
            self._storage_reader.download_to(reference=reference, target=stream)
            stream.seek(0)
            try:
                parquet_file = pq.ParquetFile(stream)
                columns = _selected_columns(parquet_file.schema.names)
                for record_batch in parquet_file.iter_batches(
                    batch_size=self.raw_batch_size,
                    columns=columns,
                ):
                    raw = record_batch.to_pandas()
                    normalized_tags = raw['id_tag'].astype('string').str.strip().str.upper()
                    selected_rows = raw[normalized_tags.isin(aliases)]
                    if not selected_rows.empty:
                        raw_frames.append(selected_rows.copy())
            except (pa.ArrowException, OSError, ValueError) as error:
                raise NotPiiSourceError('NotPII blob is not a valid Parquet file') from error

        raw_data = (
            pd.concat(raw_frames, ignore_index=True)
            if raw_frames
            else pd.DataFrame(columns=_REQUIRED_COLUMNS)
        )
        return NotPiiBatch(
            message_id=blob_message.message_id,
            data=build_wide(
                dataframe=raw_data,
                aliases_by_tag_name=aliases,
                expected_aliases=tuple(item.alias for item in selected),
            ),
            extraction_mode=extraction_mode,
        )


def decode_message(message: ServiceBusMessage) -> NotPiiBlobMessage:
    try:
        payload = json.loads(message.decode_text())
    except (UnicodeError, json.JSONDecodeError) as error:
        raise NotPiiSourceError('NotPII message body is not valid JSON') from error
    if not isinstance(payload, dict):
        raise NotPiiSourceError('NotPII message body must be a JSON object')
    message_id = optional_text(payload.get('id')) or optional_text(message.message_id)
    url = optional_text(payload.get('url'))
    sas_token = optional_text(payload.get('SasToken')) or optional_text(payload.get('sasToken'))
    if message_id is None or url is None or sas_token is None:
        raise NotPiiSourceError('NotPII message requires id, url and SasToken')
    return NotPiiBlobMessage(
        message_id=message_id,
        url=url,
        sas_token=sas_token,
        event_time_utc=optional_datetime(payload.get('eventTime')),
    )


def _selected_columns(available_columns: list[str]) -> list[str]:
    available = set(available_columns)
    missing = set(_REQUIRED_COLUMNS) - available
    if missing:
        raise NotPiiSourceError(
            'NotPII Parquet data is missing required columns: ' + ', '.join(sorted(missing))
        )
    return [column for column in (*_REQUIRED_COLUMNS, *_OPTIONAL_COLUMNS) if column in available]
