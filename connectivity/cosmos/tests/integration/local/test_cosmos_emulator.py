from __future__ import annotations

import os
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

from atlanticus.connectivity.cosmos import (
    CosmosClient,
    CosmosConflictError,
    CosmosContainerSpec,
    CosmosItemNotFoundError,
    CosmosPatchOperation,
    CosmosPreconditionFailedError,
    CosmosProvisioner,
    CosmosResultLimitError,
    CosmosSettings,
)

pytestmark = pytest.mark.integration

_RUN = os.getenv('ATLANTICUS_RUN_COSMOS_INTEGRATION') == '1'
_READY_TIMEOUT_SECONDS = 120.0
_READY_INTERVAL_SECONDS = 1.0


@pytest.mark.skipif(not _RUN, reason='Cosmos emulator integration is disabled')
def test_cosmos_contract_against_linux_emulator() -> None:
    _wait_until_ready()

    suffix = uuid.uuid4().hex[:8]
    database_name = f'atlanticus-it-{suffix}'
    container_name = f'items-{suffix}'
    settings = CosmosSettings(
        endpoint=os.environ['ATLANTICUS_COSMOS_ENDPOINT'],
        key=os.environ['ATLANTICUS_COSMOS_KEY'],
        database_name=database_name,
        allow_insecure_http=True,
        max_query_items=10,
        page_size=7,
    )

    client = CosmosClient(settings=settings)
    provisioner = CosmosProvisioner(client=client)
    assert provisioner.ensure_database() is True
    spec = CosmosContainerSpec(
        name=container_name,
        partition_key_path='/scope',
        default_ttl_seconds=3600,
    )
    assert provisioner.ensure_containers([spec]) == (container_name,)
    assert provisioner.ensure_containers([spec]) == ()
    provisioner.validate_containers([spec])
    assert client.health_check() is True

    created = client.create_item(
        container_name=container_name,
        item={'id': 'primary', 'scope': 'integration', 'value': 1},
        include_metadata=True,
    )
    assert created['_etag']
    assert client.read_item(
        container_name=container_name,
        item_id='primary',
        partition_key='integration',
    ) == {'id': 'primary', 'scope': 'integration', 'value': 1}
    assert (
        client.find_item(
            container_name=container_name,
            item_id='missing',
            partition_key='integration',
        )
        is None
    )

    with pytest.raises(CosmosConflictError):
        client.create_item(
            container_name=container_name,
            item={'id': 'primary', 'scope': 'integration'},
        )

    upserted = client.upsert_item(
        container_name=container_name,
        item={
            'id': 'primary',
            'scope': 'integration',
            'value': 2,
        },
        include_metadata=True,
    )
    second_etag = upserted['_etag']
    assert client.read_item(
        container_name=container_name,
        item_id='primary',
        partition_key='integration',
    ) == {'id': 'primary', 'scope': 'integration', 'value': 2}

    patched = client.patch_item(
        container_name=container_name,
        item_id='primary',
        partition_key='integration',
        operations=[CosmosPatchOperation('set', '/value', 4)],
        if_match_etag=second_etag,
        include_metadata=True,
    )
    assert patched['value'] == 4
    with pytest.raises(CosmosPreconditionFailedError):
        client.patch_item(
            container_name=container_name,
            item_id='primary',
            partition_key='integration',
            operations=[CosmosPatchOperation('set', '/value', 5)],
            if_match_etag=second_etag,
        )

    for index in range(25):
        client.upsert_item(
            container_name=container_name,
            item={
                'id': f'item-{index:02d}',
                'scope': 'integration',
                'value': index,
            },
        )

    parameters = [{'name': '@scope', 'value': 'integration'}]
    query = 'SELECT * FROM c WHERE c.scope = @scope'
    with pytest.raises(CosmosResultLimitError):
        client.query_items(
            container_name=container_name,
            query=query,
            parameters=parameters,
            partition_key='integration',
        )

    all_items = tuple(
        client.iter_items(
            container_name=container_name,
            query=query,
            parameters=parameters,
            partition_key='integration',
            page_size=7,
        )
    )
    assert len(all_items) == 26

    values = client.query_values(
        container_name=container_name,
        query='SELECT VALUE COUNT(1) FROM c WHERE c.scope = @scope',
        parameters=parameters,
        partition_key='integration',
        max_items=2,
    )
    assert values == (26,)

    paged_ids: list[str] = []
    token: str | None = None
    while True:
        page = client.query_page(
            container_name=container_name,
            query=query,
            parameters=parameters,
            partition_key='integration',
            page_size=7,
            continuation_token=token,
        )
        paged_ids.extend(item['id'] for item in page.items)
        token = page.continuation_token
        if token is None:
            break
    assert len(paged_ids) == 26
    assert len(set(paged_ids)) == 26

    cross_partition = client.query_items(
        container_name=container_name,
        query='SELECT * FROM c WHERE c.id = @id',
        parameters=[{'name': '@id', 'value': 'primary'}],
        cross_partition=True,
        max_items=2,
    )
    assert cross_partition[0]['id'] == 'primary'

    client.delete_item(
        container_name=container_name,
        item_id='primary',
        partition_key='integration',
        if_match_etag=patched['_etag'],
    )
    with pytest.raises(CosmosItemNotFoundError):
        client.read_item(
            container_name=container_name,
            item_id='primary',
            partition_key='integration',
        )

    client.close()


def _wait_until_ready() -> None:
    ready_url = os.environ['ATLANTICUS_COSMOS_READY_URL']
    deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
    last_error: BaseException | None = None

    while time.monotonic() < deadline:
        try:
            with urlopen(ready_url, timeout=2.0) as response:
                if 200 <= response.status < 300:
                    return
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            last_error = error
        time.sleep(_READY_INTERVAL_SECONDS)

    raise RuntimeError('Cosmos emulator did not become ready') from last_error
