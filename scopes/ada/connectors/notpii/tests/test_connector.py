from __future__ import annotations

import json
from io import BytesIO

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ada.connectors.notpii import NotPiiConnector, decode_message
from atlanticus.connectivity.service_bus import ServiceBusMessage
from atlanticus.connectivity.storage import StorageSasReader
from atlanticus.integrations.pi.contracts import (
    NotPiiSource,
    PiCatalog,
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
)


def _message() -> ServiceBusMessage:
    return ServiceBusMessage(
        body=json.dumps(
            {
                'id': 'message-1',
                'url': 'https://storage.example/container/file.parquet',
                'SasToken': 'secret-token',
                'eventTime': '2026-08-15T12:00:00Z',
            }
        ).encode(),
        message_id='fallback',
    )


def _catalog() -> PiCatalog:
    return PiCatalog(
        source=NotPiiSource(),
        definitions=(
            PiTagDefinition(
                tag_name='TAG.001',
                alias='tag_001',
                value_kind=PiValueKind.NUMBER,
                extraction_mode=PiExtractionMode.INTERPOLATED,
                materializations=(PiMaterialization.LATEST,),
            ),
        ),
    )


def _parquet_bytes() -> bytes:
    stream = BytesIO()
    pq.write_table(
        pa.table(
            {
                'timestamp': pa.array(
                    [
                        pd.Timestamp('2026-08-15T12:00:00Z'),
                        pd.Timestamp('2026-08-15T12:00:00Z'),
                        pd.Timestamp('2026-08-15T12:00:30Z'),
                    ]
                ),
                'timestamp_ingesta': pa.array(
                    [
                        pd.Timestamp('2026-08-15T12:00:01Z'),
                        pd.Timestamp('2026-08-15T12:00:02Z'),
                        pd.Timestamp('2026-08-15T12:00:31Z'),
                    ]
                ),
                'id_tag': ['TAG.001', 'TAG.001', 'TAG.001'],
                'valor': pa.array([10.0, None, 20.0], type=pa.float64()),
                'unused': ['x', 'y', 'z'],
            }
        ),
        stream,
    )
    return stream.getvalue()


def test_message_decoder_does_not_expose_sas() -> None:
    decoded = decode_message(_message())
    assert decoded.message_id == 'message-1'
    assert 'secret-token' not in repr(decoded)


def test_connector_reads_parquet_and_filters_catalog_tags(monkeypatch) -> None:
    reader = StorageSasReader()
    content = _parquet_bytes()

    def download_to(*, reference, target):
        target.write(content)
        return len(content)

    monkeypatch.setattr(reader, 'download_to', download_to)
    connector = NotPiiConnector(storage_reader=reader)
    result = connector.read(
        message=_message(),
        catalog=_catalog(),
        extraction_mode=PiExtractionMode.INTERPOLATED,
    )

    assert list(result.data.columns) == ['timestamp_utc', 'tag_001']
    assert pd.isna(result.data.loc[0, 'tag_001'])
    assert result.data.loc[1, 'tag_001'] == 20.0
    assert result.message_id == 'message-1'
