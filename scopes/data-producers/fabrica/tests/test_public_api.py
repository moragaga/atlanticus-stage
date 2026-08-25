import atlanticus.data_producers.fabrica as producer


def test_public_api_exposes_fabrica_producer_vocabulary() -> None:
    exported = set(producer.__all__)
    assert 'FabricaStorageConnection' in exported
    assert 'FabricaStorageSource' in exported
    assert 'FabricaMaterializer' in exported
    assert 'FabricaProducerState' in exported
    assert 'build_fabrica_data_producer' in exported
    assert producer.__version__ == '0.1.1'
