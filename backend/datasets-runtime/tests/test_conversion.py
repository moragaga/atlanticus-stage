from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pyarrow as pa
import pytest

from atlanticus.datasets.runtime import (
    DatasetConversionError,
    DatasetRuntimeValidationError,
    to_arrow_table,
    to_pandas_dataframe,
)


def test_dataframe_to_arrow_drops_index_without_mutating_input() -> None:
    dataframe = pd.DataFrame(
        {
            'timestamp': pd.to_datetime(
                ('2026-07-21T10:00:00Z', '2026-07-21T11:00:00Z'),
                utc=True,
            ),
            'value': pd.array((1.0, None), dtype='Float64'),
        },
        index=pd.Index((100, 101), name='event_id'),
    )
    before = dataframe.copy(deep=True)

    table = to_arrow_table(dataframe)

    assert table.column_names == ['timestamp', 'value']
    assert table.schema.field('timestamp').type == pa.timestamp('us', tz='UTC')
    assert table.schema.metadata is None
    pd.testing.assert_frame_equal(dataframe, before)


def test_arrow_input_is_returned_without_an_unnecessary_conversion() -> None:
    table = pa.table({'value': [1, 2]}).replace_schema_metadata({b'owner': b'ada'})

    assert to_arrow_table(table) is table
    assert table.schema.metadata == {b'owner': b'ada'}


def test_arrow_to_pandas_returns_a_new_dataframe_each_time() -> None:
    table = pa.table(
        {
            'timestamp': pa.array(
                (datetime(2026, 7, 21, 10, tzinfo=UTC),),
                type=pa.timestamp('us', tz='UTC'),
            ),
            'value': pa.array((None,), type=pa.float64()),
        }
    )

    first = to_pandas_dataframe(table)
    second = to_pandas_dataframe(table)
    first.loc[0, 'value'] = 99.0

    assert first is not second
    assert pd.isna(second.loc[0, 'value'])
    assert str(second['timestamp'].dtype) == 'datetime64[us, UTC]'


@pytest.mark.parametrize(
    'data',
    (
        {'value': [1]},
        [1, 2],
        'invalid',
    ),
)
def test_conversion_rejects_types_outside_the_tabular_contract(data: object) -> None:
    with pytest.raises(DatasetRuntimeValidationError):
        to_arrow_table(data)  # type: ignore[arg-type]


def test_conversion_rejects_non_string_empty_and_duplicate_columns() -> None:
    invalid_frames = (
        pd.DataFrame([[1]], columns=[1]),
        pd.DataFrame([[1]], columns=['']),
        pd.DataFrame([[1, 2]], columns=['value', 'value']),
    )

    for dataframe in invalid_frames:
        with pytest.raises(DatasetRuntimeValidationError):
            to_arrow_table(dataframe)


def test_conversion_wraps_an_unrepresentable_pandas_value() -> None:
    dataframe = pd.DataFrame({'value': [object()]})

    with pytest.raises(DatasetConversionError) as captured:
        to_arrow_table(dataframe)

    assert isinstance(captured.value.__cause__, pa.ArrowException)
