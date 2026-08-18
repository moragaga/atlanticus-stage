from __future__ import annotations

import re

from atlanticus.data_producers.remanentes import (
    RemanentesRowsStreamDefinition,
    RemanentesStocksStreamDefinition,
    RemanentesStreamDefinition,
    StockMetricDefinition,
)

_SOURCE_PATTERN = re.compile(
    r'.*/year=(?P<year>\d{4})/month=(?P<month>\d{2})/day=(?P<day>\d{2})/'
    r'data_(?P<file_date>\d{8})_(?P<file_time>\d{4})\.parquet$'
)

STOCK_METRICS = (
    StockMetricDefinition('STOC_2960', 'stock_2960'),
    StockMetricDefinition('STOC_2961', 'stock_2961'),
    StockMetricDefinition('STOC_2988', 'stock_2988'),
    StockMetricDefinition('STOC_3005', 'stock_3005'),
    StockMetricDefinition('STOC_3006', 'stock_3006'),
    StockMetricDefinition('STOC_3017', 'stock_3017'),
    StockMetricDefinition('STOC_3018', 'stock_3018'),
    StockMetricDefinition('STOC_3020', 'stock_3020'),
    StockMetricDefinition('STOC_3050', 'stock_3050'),
    StockMetricDefinition('STOC_3060', 'stock_3060'),
    StockMetricDefinition('STOC_3080', 'stock_3080'),
    StockMetricDefinition('STOC_3755', 'stock_3755'),
    StockMetricDefinition('STOC_2810', 'stock_2810'),
)


def build_catalog(
    *,
    source_timezone_name: str = 'America/Santiago',
) -> tuple[RemanentesStreamDefinition, ...]:
    return (
        RemanentesStocksStreamDefinition(
            stream_key='stocks',
            source_prefix='remanentes/stocks',
            source_filename_pattern=_SOURCE_PATTERN,
            source_timezone_name=source_timezone_name,
            stock_metrics=STOCK_METRICS,
        ),
        RemanentesRowsStreamDefinition(
            stream_key='extraibles',
            source_prefix='remanentes/extraibles',
            source_filename_pattern=_SOURCE_PATTERN,
            source_timezone_name=source_timezone_name,
        ),
        RemanentesRowsStreamDefinition(
            stream_key='no_extraibles',
            source_prefix='remanentes/no_extraibles',
            source_filename_pattern=_SOURCE_PATTERN,
            source_timezone_name=source_timezone_name,
        ),
    )
