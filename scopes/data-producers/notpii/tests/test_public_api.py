import atlanticus.data_producers.notpii as producer


def test_public_api_contains_only_data_producer_vocabulary() -> None:
    exported = set(producer.__all__)

    assert 'NotPiiDataProducerError' in exported
    assert 'NotPiiDataProducerConfigurationError' in exported
    assert 'NotPiiConnectorError' not in exported
    assert 'NotPiiConfigurationError' not in exported
    assert 'NotPiiProcessError' not in exported
    assert 'NotPiiProcessConfigurationError' not in exported
