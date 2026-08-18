from __future__ import annotations

import re

from atlanticus.data_producers.remanentes import (
    RemanentesRowsStreamDefinition,
    RemanentesStocksStreamDefinition,
    RemanentesStreamDefinition,
    StockMetricDefinition,
)

_PATTERN = re.compile(
    r'.*/year=(?P<year>\d{4})/month=(?P<month>\d{2})/day=(?P<day>\d{2})/'
    r'data_(?P<file_date>\d{8})_(?P<file_time>\d{4})\.parquet$'
)


def build_test_catalog() -> tuple[RemanentesStreamDefinition, ...]:
    return (
        RemanentesStocksStreamDefinition(
            stream_key='stocks',
            source_prefix='remanentes/stocks',
            source_filename_pattern=_PATTERN,
            stock_metrics=(
                StockMetricDefinition('STOC_2960', 'stock_2960'),
                StockMetricDefinition('STOC_2961', 'stock_2961'),
                StockMetricDefinition('STOC_3018', 'stock_3018'),
            ),
        ),
        RemanentesRowsStreamDefinition(
            stream_key='extraibles',
            source_prefix='remanentes/extraibles',
            source_filename_pattern=_PATTERN,
        ),
    )
