from __future__ import annotations

import re
from datetime import UTC, date, datetime

import pytest

from atlanticus.data_producers.remanentes import (
    RemanentesContractError,
    RemanentesRowsStreamDefinition,
    RemanentesStocksStreamDefinition,
    StockMetricDefinition,
    parse_source_timestamp,
)

_PATTERN = re.compile(
    r'.*/year=(?P<year>\d{4})/month=(?P<month>\d{2})/day=(?P<day>\d{2})/'
    r'data_(?P<file_date>\d{8})_(?P<file_time>\d{4})\.parquet$'
)


def _stocks() -> RemanentesStocksStreamDefinition:
    return RemanentesStocksStreamDefinition(
        stream_key='stocks',
        source_prefix='remanentes/stocks',
        source_filename_pattern=_PATTERN,
        stock_metrics=(StockMetricDefinition('STOC_1', 'stock_1'),),
    )


def test_definition_builds_source_day_prefix() -> None:
    assert _stocks().source_day_prefix(date(2026, 8, 10)) == (
        'remanentes/stocks/year=2026/month=08/day=10/'
    )


def test_definition_resolves_santiago_local_day() -> None:
    assert _stocks().source_local_date(datetime(2026, 8, 11, 2, 0, tzinfo=UTC)) == date(2026, 8, 10)


def test_source_timestamp_uses_santiago_and_returns_utc() -> None:
    assert parse_source_timestamp(
        definition=_stocks(),
        blob_name='remanentes/stocks/year=2026/month=08/day=10/data_20260810_2050.parquet',
    ) == datetime(2026, 8, 11, 0, 50, tzinfo=UTC)


def test_source_timestamp_rejects_partition_filename_date_mismatch() -> None:
    assert (
        parse_source_timestamp(
            definition=_stocks(),
            blob_name='remanentes/stocks/year=2026/month=08/day=10/data_20260811_2050.parquet',
        )
        is None
    )


def test_stocks_definition_rejects_duplicate_metrics() -> None:
    with pytest.raises(RemanentesContractError, match='unique'):
        RemanentesStocksStreamDefinition(
            stream_key='stocks',
            source_prefix='x',
            source_filename_pattern=_PATTERN,
            stock_metrics=(
                StockMetricDefinition('A', 'a'),
                StockMetricDefinition('A', 'b'),
            ),
        )


def test_rows_definition_has_no_stock_contract() -> None:
    definition = RemanentesRowsStreamDefinition(
        stream_key='extraibles',
        source_prefix='remanentes/extraibles',
        source_filename_pattern=_PATTERN,
    )

    assert not hasattr(definition, 'stock_metrics')
