from datetime import UTC, datetime

import pytest

from ada.kpis.delivery import KpiDeliveryConfiguration, project_kpi_latest
from ada.processes.kpis_delivery import (
    KPI_LATEST_DELIVERY_CONTAINER_NAME,
    KpiDeliveryRepositoryError,
    KpiLatestPublicationStatus,
    KpiLatestSnapshotRepository,
)


class FakeCosmosClient:
    def __init__(self, current: dict[str, object] | None = None) -> None:
        self.current = current
        self.find_calls: list[dict[str, object]] = []
        self.upsert_calls: list[dict[str, object]] = []

    def find_item(self, *, container_name, item_id, partition_key, include_metadata=False):
        self.find_calls.append(
            {
                'container_name': container_name,
                'item_id': item_id,
                'partition_key': partition_key,
                'include_metadata': include_metadata,
            }
        )
        return self.current

    def upsert_item(self, *, container_name, item, include_metadata=False):
        self.upsert_calls.append(
            {
                'container_name': container_name,
                'item': item,
                'include_metadata': include_metadata,
            }
        )
        self.current = item
        return item


def _snapshot():
    configuration = KpiDeliveryConfiguration.from_document(
        {
            'id': 'kpis',
            'partition_key': 'kpis',
            'document_type': 'ada_kpi_configuration_projection',
            'schema_version': 1,
            'revision': 'config-1',
            'tool_projection_revision': 'tools-1',
            'configuration': {'bindings': []},
        }
    )
    return project_kpi_latest(
        evaluation=None,
        configuration=configuration,
        watermark=None,
        published_at_utc=datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
    )


def test_publish_uses_internal_container_and_skips_unchanged_revision() -> None:
    client = FakeCosmosClient()
    snapshot = _snapshot()
    repository = KpiLatestSnapshotRepository(client=client)

    first = repository.publish(snapshot)
    second = repository.publish(snapshot)

    assert first.status is KpiLatestPublicationStatus.PUBLISHED
    assert second.status is KpiLatestPublicationStatus.UNCHANGED
    assert client.find_calls[0]['container_name'] == KPI_LATEST_DELIVERY_CONTAINER_NAME
    assert client.upsert_calls[0]['container_name'] == KPI_LATEST_DELIVERY_CONTAINER_NAME


@pytest.mark.parametrize(
    'current',
    [
        {'id': 'latest', 'partition_id': 'kpis'},
        {'id': 'latest', 'partition_id': 'kpis', 'manifest': None},
        {'id': 'latest', 'partition_id': 'kpis', 'manifest': {}},
        {'id': 'latest', 'partition_id': 'kpis', 'manifest': {'revision': ''}},
        {'id': 'latest', 'partition_id': 'kpis', 'manifest': {'revision': ' rev-1 '}},
    ],
)
def test_publish_rejects_invalid_existing_snapshot_without_overwriting(current) -> None:
    client = FakeCosmosClient(current=current)

    with pytest.raises(KpiDeliveryRepositoryError):
        KpiLatestSnapshotRepository(client=client).publish(_snapshot())

    assert client.upsert_calls == []
