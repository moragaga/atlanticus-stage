from datetime import UTC, datetime

import pandas as pd
import pyarrow as pa

from atlanticus.data_producers.notpii import NotPiiBatch
from atlanticus.data_producers.notpii.materialization import NotPiiMaterializer
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.integrations.pi.contracts import (
    NotPiiSource,
    PiCatalog,
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
)


def _definition(tag: str, alias: str) -> PiTagDefinition:
    return PiTagDefinition(
        tag_name=tag,
        alias=alias,
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.RECORDED,
        materializations=(PiMaterialization.DAILY,),
    )


def test_recorded_sparse_replay_preserves_existing_values(tmp_path) -> None:
    catalog = PiCatalog(
        source=NotPiiSource(),
        definitions=(_definition('TAG_A', 'a'), _definition('TAG_B', 'b')),
    )
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / 'datasets'))
    materializer = NotPiiMaterializer(
        runtime=runtime,
        catalog=catalog,
        extraction_mode=PiExtractionMode.RECORDED,
    )
    timestamp = datetime(2026, 8, 15, 12, 0, 3, tzinfo=UTC)

    materializer.publish(
        NotPiiBatch(
            message_id='first',
            data=pd.DataFrame({'timestamp_utc': [timestamp], 'a': [1.0], 'b': [2.0]}),
            extraction_mode=PiExtractionMode.RECORDED,
        )
    )
    materializer.publish(
        NotPiiBatch(
            message_id='replay',
            data=pd.DataFrame({'timestamp_utc': [timestamp], 'a': [None], 'b': [3.0]}),
            extraction_mode=PiExtractionMode.RECORDED,
        )
    )

    target = materializer.dataset.resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '15'},
    )
    table = runtime.read_table(definition=materializer.dataset, target=target).table
    assert table.schema.field('timestamp_utc').type == pa.timestamp('us', tz='UTC')
    assert table.to_pydict() == {
        'timestamp_utc': [timestamp],
        'a': [1.0],
        'b': [3.0],
    }
