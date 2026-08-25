from __future__ import annotations

import atlanticus.data_producers.remanentes as producer


def test_public_api_exposes_explicit_remanentes_contracts() -> None:
    exported = set(producer.__all__)
    assert 'RemanentesRowsStreamDefinition' in exported
    assert 'RemanentesStocksStreamDefinition' in exported
    assert 'StockMetricDefinition' in exported
    assert 'RemanentesStorageConnection' in exported
    assert 'RemanentesStorageSource' in exported
    assert 'RemanentesMaterializer' in exported
    assert 'RemanentesLatestMaterializer' in exported
    assert 'RemanentesProducerState' in exported
    assert 'build_remanentes_data_producer' in exported
    assert 'RemanentesTransformKind' not in exported
    assert producer.__version__ == '0.1.1'
