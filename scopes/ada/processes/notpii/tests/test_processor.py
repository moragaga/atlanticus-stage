from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ada.connectors.notpii import NotPiiBatch, NotPiiConnector
from ada.processes.notpii.processor import _coalesce_by_timestamp, _source_last_updated_at_utc
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


def test_coalesce_preserves_values_from_multiple_messages_at_same_timestamp() -> None:
    timestamp = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    frame = pd.DataFrame(
        {
            'timestamp_utc': [timestamp, timestamp],
            'a': [1.0, None],
            'b': [None, 2.0],
        }
    )
    result = _coalesce_by_timestamp(frame)
    assert len(result) == 1
    assert result.loc[0, 'a'] == 1.0
    assert result.loc[0, 'b'] == 2.0
    assert _source_last_updated_at_utc(result) == timestamp


def test_batch_model_requires_mode_and_timestamp() -> None:
    batch = NotPiiBatch(
        message_id='m1',
        data=pd.DataFrame({'timestamp_utc': [datetime(2026, 8, 15, tzinfo=UTC)]}),
        extraction_mode=PiExtractionMode.RECORDED,
    )
    assert batch.message_id == 'm1'


def test_process_gate_exercises_connector_parquet_filtering(monkeypatch) -> None:
    stream = BytesIO()
    pq.write_table(
        pa.table(
            {
                'timestamp': pa.array(
                    [
                        pd.Timestamp('2026-08-15T12:00:00Z'),
                        pd.Timestamp('2026-08-15T12:00:30Z'),
                    ]
                ),
                'id_tag': ['TAG.001', 'IGNORED'],
                'valor': pa.array([10.0, 999.0], type=pa.float64()),
                'unused': ['x', 'y'],
            }
        ),
        stream,
    )
    reader = StorageSasReader()

    def download_to(*, reference, target):
        target.write(stream.getvalue())
        return len(stream.getvalue())

    monkeypatch.setattr(reader, 'download_to', download_to)
    connector = NotPiiConnector(storage_reader=reader)
    message = ServiceBusMessage(
        body=json.dumps(
            {
                'id': 'message-1',
                'url': 'https://storage.example/container/file.parquet',
                'SasToken': 'secret-token',
            }
        ).encode(),
        message_id='fallback',
    )
    catalog = PiCatalog(
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

    result = connector.read(
        message=message,
        catalog=catalog,
        extraction_mode=PiExtractionMode.INTERPOLATED,
    )

    assert list(result.data.columns) == ['timestamp_utc', 'tag_001']
    assert result.data['tag_001'].tolist() == [10.0]
