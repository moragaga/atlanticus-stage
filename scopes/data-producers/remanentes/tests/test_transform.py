from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pyarrow as pa

from atlanticus.data_producers.remanentes.transform import merge_snapshot, transform_snapshot

from .support import build_test_catalog


def _definition(key: str):
    return next(item for item in build_test_catalog() if item.stream_key == key)


def test_stocks_are_pivoted_to_explicit_columns_with_typed_nulls() -> None:
    table = pa.table(
        {
            'STOCK': ['STOC_2960', 'STOC_2961', 'STOC_UNKNOWN'],
            'Ton (kt)': [
                '{"type":"BigInt","value":"27"}',
                '{"type":"BigInt","value":"9464"}',
                '{"type":"BigInt","value":"5"}',
            ],
        }
    )
    result = transform_snapshot(
        table=table,
        definition=_definition('stocks'),
        source_timestamp_utc=datetime(2026, 8, 11, 0, 50, tzinfo=UTC),
    )

    assert len(result.dataframe) == 1
    assert result.dataframe.loc[0, 'stock_2960'] == 27.0
    assert result.dataframe.loc[0, 'stock_2961'] == 9464.0
    assert pd.isna(result.dataframe.loc[0, 'stock_3018'])
    assert result.dataframe['stock_3018'].dtype == 'Float64'
    assert result.unknown_source_values == ('STOC_UNKNOWN',)
    assert result.missing_metric_keys == ('stock_3018',)


def test_duplicate_stock_uses_last_non_null_observation() -> None:
    table = pa.table(
        {
            'STOCK': ['STOC_2960', 'STOC_2960', 'STOC_2960'],
            'Ton (kt)': [10, None, 30],
        }
    )

    result = transform_snapshot(
        table=table,
        definition=_definition('stocks'),
        source_timestamp_utc=datetime(2026, 8, 11, 0, 50, tzinfo=UTC),
    )

    assert result.dataframe.loc[0, 'stock_2960'] == 30.0


def test_extraibles_keep_source_granularity_and_normalize_column_names() -> None:
    table = pa.table(
        {
            'Fase': ['F11W', 'F11W'],
            'Banco': [3080, 3110],
            'Tipo de material': ['Mineral', 'Estéril'],
            'Observación': ['Rem. Extraíble', 'Rem. Extraíble'],
            'Ton (kt)': [
                '{"type":"BigInt","value":"39"}',
                '{"type":"BigInt","value":"206"}',
            ],
        }
    )

    result = transform_snapshot(
        table=table,
        definition=_definition('extraibles'),
        source_timestamp_utc=datetime(2026, 8, 11, 0, 50, tzinfo=UTC),
    )

    assert list(result.dataframe.columns) == [
        'timestamp',
        'fase',
        'banco',
        'tipo_material',
        'observacion',
        'ton_kt',
    ]
    assert len(result.dataframe) == 2
    assert result.dataframe['ton_kt'].tolist() == [39.0, 206.0]
    assert result.dataframe['tipo_material'].tolist() == ['Mineral', 'Estéril']


def test_same_snapshot_timestamp_is_replaced_without_deduplicating_rows() -> None:
    timestamp = pd.Timestamp('2026-08-11T00:50:00Z')
    previous = pd.DataFrame(
        {
            'timestamp': [timestamp, timestamp, pd.Timestamp('2026-08-11T01:00:00Z')],
            'fase': ['OLD-A', 'OLD-B', 'KEEP'],
            'banco': pd.Series([1.0, 2.0, 3.0], dtype='Float64'),
            'tipo_material': pd.Series(['A', 'B', 'C'], dtype='string'),
            'observacion': pd.Series(['x', 'y', 'z'], dtype='string'),
            'ton_kt': pd.Series([1.0, 2.0, 3.0], dtype='Float64'),
        }
    )
    incoming = pd.DataFrame(
        {
            'timestamp': [timestamp, timestamp],
            'fase': ['NEW', 'NEW'],
            'banco': pd.Series([4.0, 4.0], dtype='Float64'),
            'tipo_material': pd.Series(['M', 'M'], dtype='string'),
            'observacion': pd.Series(['a', 'a'], dtype='string'),
            'ton_kt': pd.Series([10.0, 10.0], dtype='Float64'),
        }
    )

    merged = merge_snapshot(
        current=previous,
        incoming=incoming,
        source_timestamp_utc=timestamp.to_pydatetime(),
    )

    assert merged['fase'].tolist() == ['NEW', 'NEW', 'KEEP']
    assert len(merged[merged['timestamp'].eq(timestamp)]) == 2
