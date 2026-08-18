from __future__ import annotations

import pytest

from atlanticus.data_producers.remanentes import (
    RemanentesContractError,
    StockMetricDefinition,
    validate_stock_metrics,
)


def test_stock_metric_normalizes_source_and_metric_key() -> None:
    metric = StockMetricDefinition('  stoc_2960 ', ' Stock_2960 ')

    assert metric.source_value == 'STOC_2960'
    assert metric.metric_key == 'stock_2960'


def test_stock_metric_rejects_invalid_route_key() -> None:
    with pytest.raises(RemanentesContractError, match='metric_key'):
        StockMetricDefinition('STOC_2960', 'stock/2960')


def test_stock_catalog_rejects_duplicate_source_values() -> None:
    with pytest.raises(RemanentesContractError, match='source values must be unique'):
        validate_stock_metrics(
            (
                StockMetricDefinition('A', 'a'),
                StockMetricDefinition('a', 'b'),
            )
        )
