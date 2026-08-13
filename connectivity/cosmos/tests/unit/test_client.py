from __future__ import annotations

from typing import Any

import pytest

import atlanticus.connectivity.cosmos.client as client_module
from atlanticus.connectivity.cosmos import (
    CosmosAuthenticationError,
    CosmosAuthorizationError,
    CosmosClient,
    CosmosClosedError,
    CosmosConflictError,
    CosmosItemNotFoundError,
    CosmosOperationError,
    CosmosPatchOperation,
    CosmosPreconditionFailedError,
    CosmosQueryContractError,
    CosmosResultLimitError,
    CosmosSettings,
)

from .fakes import (
    FakeClient,
    FakeContainer,
    FakeDatabase,
    FakeHttpError,
    FakeMatchConditions,
    FakePaged,
    FakePartitionKey,
)


def _client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[CosmosClient, FakeClient, FakeContainer, list[dict[str, Any]]]:
    database = FakeDatabase()
    container = database.get_container_client('alarms')
    client = FakeClient(database)
    constructor_calls: list[dict[str, Any]] = []

    def client_factory(*args: Any, **kwargs: Any) -> FakeClient:
        constructor_calls.append({'args': args, 'kwargs': kwargs})
        return client

    sdk = client_module._CosmosSdk(
        CosmosClient=client_factory,
        PartitionKey=FakePartitionKey,
        MatchConditions=FakeMatchConditions,
        CosmosHttpResponseError=FakeHttpError,
    )
    monkeypatch.setattr(client_module, '_load_cosmos_sdk', lambda: sdk)
    client_contract = CosmosClient(
        settings=CosmosSettings(
            endpoint='https://account.documents.azure.com',
            key='  private-key  ',
            database_name='atlanticus',
            max_query_items=2,
            page_size=2,
        )
    )
    return client_contract, client, container, constructor_calls


def test_replace_item_is_not_part_of_the_public_contract() -> None:
    assert not hasattr(CosmosClient, 'replace_item')


def test_single_client_is_reused_and_metadata_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, container, constructor_calls = _client(monkeypatch)
    container.responses['create_item'] = {
        'id': 'a1',
        'status': 'active',
        '_etag': 'etag-1',
        '_ts': 123,
    }

    clean = client.create_item(container_name='alarms', item={'id': 'a1'})
    complete = client.create_item(
        container_name='alarms',
        item={'id': 'a2'},
        include_metadata=True,
    )
    client.health_check()

    assert clean == {'id': 'a1', 'status': 'active'}
    assert complete['_etag'] == 'etag-1'
    assert len(constructor_calls) == 1


def test_read_is_strict_and_find_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, container, _ = _client(monkeypatch)
    container.responses['read_item'] = FakeHttpError(404, 'private item')

    with pytest.raises(CosmosItemNotFoundError) as captured:
        client.read_item(container_name='alarms', item_id='missing', partition_key='p1')

    assert captured.value.__cause__ is None
    assert 'private' not in repr(captured.value)
    assert (
        client.find_item(
            container_name='alarms',
            item_id='missing',
            partition_key='p1',
        )
        is None
    )


def test_upsert_sends_the_complete_document(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, container, _ = _client(monkeypatch)
    client.upsert_item(
        container_name='alarms',
        item={'id': 'a1', 'partition': 'p1', 'status': 'closed'},
    )

    operation, call = container.calls[0]
    assert operation == 'upsert_item'
    assert call == {
        'body': {'id': 'a1', 'partition': 'p1', 'status': 'closed'},
    }


def test_etag_is_applied_to_patch_and_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, container, _ = _client(monkeypatch)
    client.patch_item(
        container_name='alarms',
        item_id='a1',
        partition_key='p1',
        operations=[CosmosPatchOperation('set', '/status', 'active')],
        if_match_etag='etag-2',
    )
    client.delete_item(
        container_name='alarms',
        item_id='a1',
        partition_key='p1',
        if_match_etag='etag-3',
    )

    for _, call in container.calls:
        assert call['match_condition'] == FakeMatchConditions.IfNotModified
        assert call['etag'].startswith('etag-')


def test_query_contract_limit_values_page_and_iterator(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, container, _ = _client(monkeypatch)

    with pytest.raises(CosmosQueryContractError):
        client.query_items(container_name='alarms', query='SELECT * FROM c')
    with pytest.raises(CosmosQueryContractError):
        client.query_items(
            container_name='alarms',
            query='SELECT * FROM c',
            partition_key='p1',
            cross_partition=True,
        )

    container.query_paged = FakePaged([{'id': '1'}, {'id': '2'}, {'id': '3'}])
    with pytest.raises(CosmosResultLimitError):
        client.query_items(
            container_name='alarms',
            query='SELECT * FROM c',
            cross_partition=True,
        )
    assert [
        item['id']
        for item in client.iter_items(
            container_name='alarms',
            query='SELECT * FROM c',
            cross_partition=True,
        )
    ] == ['1', '2', '3']

    container.query_paged = FakePaged([1, 2])
    assert client.query_values(
        container_name='alarms',
        query='SELECT VALUE c.value FROM c',
        partition_key='p1',
    ) == (1, 2)

    container.query_paged = FakePaged(
        [],
        pages=[[{'id': '1'}, {'id': '2'}]],
        tokens=['next-token'],
    )
    page = client.query_page(
        container_name='alarms',
        query='SELECT * FROM c',
        cross_partition=True,
        continuation_token='current-token',
    )
    assert page.continuation_token == 'next-token'
    assert [item['id'] for item in page.items] == ['1', '2']
    assert container.query_paged.received_token == 'current-token'

    container.query_paged = FakePaged(
        [],
        pages=[[{'id': '3'}]],
        tokens=[''],
    )
    final_page = client.query_page(
        container_name='alarms',
        query='SELECT * FROM c',
        cross_partition=True,
    )
    assert final_page.continuation_token is None


def test_status_errors_are_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, container, _ = _client(monkeypatch)
    cases = (
        (401, CosmosAuthenticationError),
        (403, CosmosAuthorizationError),
        (409, CosmosConflictError),
        (412, CosmosPreconditionFailedError),
    )
    for status, expected in cases:
        container.responses['upsert_item'] = FakeHttpError(status, 'private-key private-query')
        with pytest.raises(expected) as captured:
            client.upsert_item(container_name='alarms', item={'id': 'a1'})
        assert 'private' not in repr(captured.value)
        assert captured.value.__cause__ is None


def test_client_is_lazy_reused_and_closed_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, sdk_client, _, constructor_calls = _client(monkeypatch)

    assert constructor_calls == []
    client.health_check()
    client.health_check()

    assert client._client is sdk_client
    assert len(constructor_calls) == 1
    assert constructor_calls[0]['kwargs']['credential'] == '  private-key  '
    assert constructor_calls[0]['kwargs']['retry_write'] == 0
    assert constructor_calls[0]['kwargs']['connection_mode'] == 'Gateway'

    client.close()
    assert sdk_client.closed is True
    assert client._client is None

    client.close()
    with pytest.raises(CosmosClosedError):
        client.health_check()


def test_context_manager_close_failure_does_not_hide_business_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, sdk_client, _, _ = _client(monkeypatch)
    client.open()
    sdk_client.close_error = RuntimeError('private close failure')

    with pytest.raises(ValueError, match='business failure'):
        with client:
            raise ValueError('business failure')


def test_close_failure_is_sanitized_when_there_is_no_business_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, sdk_client, _, _ = _client(monkeypatch)
    client.open()
    sdk_client.close_error = RuntimeError('private close failure')

    with pytest.raises(CosmosOperationError) as captured:
        client.close()

    assert 'private' not in repr(captured.value)
    assert captured.value.__cause__ is None
