from datetime import UTC, datetime

import pyarrow as pa
import pytest

from ada.processes.dispatch.curation import curate_dispatch_table, source_last_update_utc
from ada.processes.dispatch.errors import DispatchSchemaError


def test_curation_casts_dispatch_values_and_normalizes_datetime(shift_definition) -> None:
    raw = pa.table(
        {
            'shift_id': [260817002],
            'moment': [datetime(2026, 8, 17, 18, 4, 49)],
            'value': [2],
        }
    )

    curated = curate_dispatch_table(definition=shift_definition, table=raw)

    assert curated.schema.field('shift_id').type == pa.int64()
    assert curated.schema.field('value').type == pa.float64()
    assert curated.schema.field('moment').type == pa.timestamp('us', tz='UTC')
    assert source_last_update_utc(definition=shift_definition, table=curated) == datetime(
        2026, 8, 17, 22, 4, 49, tzinfo=UTC
    )


def test_required_column_rejects_nulls(shift_definition) -> None:
    raw = pa.table({'shift_id': [None], 'moment': [None], 'value': [1.0]})

    with pytest.raises(DispatchSchemaError, match='required column shift_id'):
        curate_dispatch_table(definition=shift_definition, table=raw)
