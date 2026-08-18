from __future__ import annotations

from datetime import UTC, datetime

from ada.processes.remanentes.catalog import STOCK_METRICS, build_catalog
from atlanticus.data_producers.remanentes import (
    RemanentesRowsStreamDefinition,
    RemanentesStocksStreamDefinition,
    parse_source_timestamp,
)


def test_catalog_declares_three_streams_and_thirteen_stocks() -> None:
    catalog = {item.stream_key: item for item in build_catalog()}

    assert tuple(catalog) == ('stocks', 'extraibles', 'no_extraibles')
    assert len(STOCK_METRICS) == 13
    assert isinstance(catalog['stocks'], RemanentesStocksStreamDefinition)
    assert catalog['stocks'].stock_metrics[0].source_value == 'STOC_2960'
    assert catalog['stocks'].stock_metrics[-1].metric_key == 'stock_2810'
    assert isinstance(catalog['extraibles'], RemanentesRowsStreamDefinition)
    assert isinstance(catalog['no_extraibles'], RemanentesRowsStreamDefinition)


def test_catalog_preserves_source_paths_and_local_timestamp_semantics() -> None:
    definition = build_catalog()[0]

    assert definition.source_prefix == 'remanentes/stocks'
    assert parse_source_timestamp(
        definition=definition,
        blob_name='remanentes/stocks/year=2026/month=08/day=10/data_20260810_2050.parquet',
    ) == datetime(2026, 8, 11, 0, 50, tzinfo=UTC)
