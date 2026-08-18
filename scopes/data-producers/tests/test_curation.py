from datetime import UTC, datetime

import pyarrow as pa
import pytest

from atlanticus.data_producers.sql import (
    SqlDataProducerSchemaError,
    curate_table,
    source_last_update_utc,
)


def test_curation_casts_values_and_normalizes_datetime(scoped_definition) -> None:
    raw = pa.table(
        {
            'scope_id': [1],
            'moment': [datetime(2026, 8, 18, 12, 0)],
            'value': [2],
        }
    )

    curated = curate_table(definition=scoped_definition, table=raw)

    assert curated.schema.field('scope_id').type == pa.int64()
    assert curated.schema.field('value').type == pa.float64()
    assert source_last_update_utc(definition=scoped_definition, table=curated) == datetime(
        2026, 8, 18, 12, 0, tzinfo=UTC
    )


def test_required_column_rejects_nulls(scoped_definition) -> None:
    raw = pa.table({'scope_id': [None], 'moment': [None], 'value': [1.0]})

    with pytest.raises(SqlDataProducerSchemaError, match='required column scope_id'):
        curate_table(definition=scoped_definition, table=raw)
