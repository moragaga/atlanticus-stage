from __future__ import annotations

import pytest

import atlanticus.connectivity.cosmos.client as client_module
from atlanticus.connectivity.cosmos import (
    CosmosClient,
    CosmosContainerDefinitionMismatchError,
    CosmosContainerSpec,
    CosmosProvisioner,
    CosmosSettings,
)

from .fakes import (
    FakeClient,
    FakeDatabase,
    FakeHttpError,
    FakeMatchConditions,
    FakePartitionKey,
)


def _provisioner(monkeypatch: pytest.MonkeyPatch):
    database = FakeDatabase()
    client = FakeClient(database)
    sdk = client_module._CosmosSdk(
        CosmosClient=lambda *args, **kwargs: client,
        PartitionKey=FakePartitionKey,
        MatchConditions=FakeMatchConditions,
        CosmosHttpResponseError=FakeHttpError,
    )
    monkeypatch.setattr(client_module, '_load_cosmos_sdk', lambda: sdk)
    client_contract = CosmosClient(
        settings=CosmosSettings(
            endpoint='http://cosmos:8081',
            key='emulator-key',
            database_name='atlanticus-test',
            allow_insecure_http=True,
        )
    )
    return CosmosProvisioner(client=client_contract), client_contract, database, client


def test_database_and_containers_are_explicit_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provisioner, client_contract, database, client = _provisioner(monkeypatch)
    database.read_error = FakeHttpError(404)

    assert provisioner.ensure_database() is True
    assert client.database_create_calls == ['atlanticus-test']

    missing = database.get_container_client('alarms')
    missing.read_error = FakeHttpError(404)
    specs = [
        CosmosContainerSpec(
            name='alarms',
            partition_key_path='/application',
            default_ttl_seconds=86_400,
        )
    ]
    assert provisioner.ensure_containers(specs) == ('alarms',)
    assert provisioner.ensure_containers(specs) == ()
    assert client_contract._client is client
    assert database.create_calls[0]['default_ttl'] == 86_400


def test_existing_partition_or_ttl_drift_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    provisioner, _, database, _ = _provisioner(monkeypatch)
    container = database.get_container_client('alarms')
    container.properties = {
        'id': 'alarms',
        'partitionKey': {'paths': ['/wrong']},
        'defaultTtl': 100,
    }

    with pytest.raises(CosmosContainerDefinitionMismatchError):
        provisioner.ensure_containers([CosmosContainerSpec('alarms', '/application', 86_400)])


def test_provisioner_validates_drift_instead_of_mutating_container_definition() -> None:
    assert not hasattr(CosmosProvisioner, 'update_container_ttl')
