from __future__ import annotations

import atlanticus.connectivity.cosmos as cosmos


def test_public_api_and_version_are_stable() -> None:
    assert cosmos.__version__ == '0.1.0'
    expected = {
        'CosmosAuthorizationError',
        'CosmosClient',
        'CosmosClosedError',
        'CosmosContainerSpec',
        'CosmosPage',
        'CosmosPatchOperation',
        'CosmosProvisioner',
        'CosmosQueryParameter',
        'CosmosSettings',
    }
    assert expected.issubset(set(cosmos.__all__))
    assert 'CosmosService' not in cosmos.__all__
    assert not hasattr(cosmos, 'CosmosService')
