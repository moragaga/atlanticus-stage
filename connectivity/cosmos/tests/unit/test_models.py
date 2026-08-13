from __future__ import annotations

import pytest

from atlanticus.connectivity.cosmos import (
    CosmosConfigurationError,
    CosmosContainerSpec,
    CosmosPage,
    CosmosPatchOperation,
    CosmosQueryParameter,
)


def test_neutral_models_validate_and_convert() -> None:
    parameter = CosmosQueryParameter(name='@status', value='active')
    operation = CosmosPatchOperation(operation='SET', path='/status', value='active')
    page = CosmosPage(items=({'id': '1'},), continuation_token='token')
    spec = CosmosContainerSpec(
        name='alarms',
        partition_key_path='/application',
        default_ttl_seconds=86_400,
    )

    assert parameter.as_sdk_value() == {'name': '@status', 'value': 'active'}
    assert operation.as_sdk_value() == {'op': 'set', 'path': '/status', 'value': 'active'}
    assert page.item_count == 1
    assert spec.default_ttl_seconds == 86_400


def test_identifiers_and_paths_do_not_silently_trim() -> None:
    with pytest.raises(CosmosConfigurationError):
        CosmosQueryParameter(name=' @status ', value='active')
    with pytest.raises(CosmosConfigurationError):
        CosmosPatchOperation(operation='set', path=' /status ', value='active')
    with pytest.raises(CosmosConfigurationError):
        CosmosContainerSpec(name=' alarms ', partition_key_path='/application')
    with pytest.raises(CosmosConfigurationError):
        CosmosContainerSpec(name='alarms?invalid', partition_key_path='/application')


def test_invalid_models_are_rejected() -> None:
    with pytest.raises(CosmosConfigurationError):
        CosmosQueryParameter(name='status', value='active')
    with pytest.raises(CosmosConfigurationError):
        CosmosPatchOperation(operation='move', path='/new')
    with pytest.raises(CosmosConfigurationError):
        CosmosContainerSpec(name='alarms', partition_key_path='application')
    with pytest.raises(CosmosConfigurationError):
        CosmosPage(items=(), continuation_token='')
