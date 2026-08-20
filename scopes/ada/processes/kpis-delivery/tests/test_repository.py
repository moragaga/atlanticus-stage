from datetime import UTC, datetime

import pytest

from ada.kpis.delivery import (
    KpiDeliveryManifest,
    KpiDeliverySnapshot,
    KpiDeliveryStatus,
    KpiDeliveryValue,
)
from ada.processes.kpis_delivery import (
    KpiDeliveryRepositoryError,
    KpiLatestPublicationStatus,
    KpiLatestSnapshotRepository,
)


class FakeCosmosClient:
    def __init__(self, current: dict[str, object] | None = None) -> None:
        self.current = current
        self.find_calls: list[dict[str, object]] = []
        self.upsert_calls: list[dict[str, object]] = []

    def find_item(
        self,
        *,
        container_name: str,
        item_id: str,
        partition_key: object,
        include_metadata: bool = False,
    ) -> dict[str, object] | None:
        self.find_calls.append(
            {
                'container_name': container_name,
                'item_id': item_id,
                'partition_key': partition_key,
                'include_metadata': include_metadata,
            }
        )
        return self.current

    def upsert_item(
        self,
        *,
        container_name: str,
        item: dict[str, object],
        include_metadata: bool = False,
    ) -> dict[str, object]:
        self.upsert_calls.append(
            {
                'container_name': container_name,
                'item': item,
                'include_metadata': include_metadata,
            }
        )
        return item


def _snapshot(*, revision: str = 'rev-1', key: str = 'tonelaje') -> KpiDeliverySnapshot:
    return KpiDeliverySnapshot(
        id='snapshot',
        partition_id='kpis',
        manifest=KpiDeliveryManifest(
            schema_version=1,
            updated_at_utc=datetime(2026, 8, 20, 15, 0, tzinfo=UTC),
            revision=revision,
        ),
        stores={
            'chancado': {
                key: KpiDeliveryValue(
                    status=KpiDeliveryStatus.MISSING,
                    value_kind=None,
                    value=None,
                )
            }
        },
    )


def _repository(client: FakeCosmosClient) -> KpiLatestSnapshotRepository:
    return KpiLatestSnapshotRepository(client=client, container_name='application-data')


def test_publish_upserts_snapshot_when_document_does_not_exist() -> None:
    client = FakeCosmosClient()
    snapshot = _snapshot()

    publication = _repository(client).publish(snapshot)

    assert publication.status is KpiLatestPublicationStatus.PUBLISHED
    assert publication.published is True
    assert publication.revision == 'rev-1'
    assert client.find_calls == [
        {
            'container_name': 'application-data',
            'item_id': 'snapshot',
            'partition_key': 'kpis',
            'include_metadata': False,
        }
    ]
    assert client.upsert_calls == [
        {
            'container_name': 'application-data',
            'item': snapshot.as_document(),
            'include_metadata': False,
        }
    ]


def test_publish_skips_write_when_revision_is_unchanged() -> None:
    client = FakeCosmosClient(
        current={
            'id': 'snapshot',
            'partition_id': 'kpis',
            'manifest': {'revision': 'rev-1'},
            'stores': {},
        }
    )

    publication = _repository(client).publish(_snapshot())

    assert publication.status is KpiLatestPublicationStatus.UNCHANGED
    assert publication.published is False
    assert publication.revision == 'rev-1'
    assert client.upsert_calls == []


def test_publish_replaces_complete_snapshot_when_revision_changes() -> None:
    client = FakeCosmosClient(
        current={
            'id': 'snapshot',
            'partition_id': 'kpis',
            'manifest': {'revision': 'rev-old'},
            'stores': {'old': {}},
        }
    )
    snapshot = _snapshot(revision='rev-new', key='nuevo')

    publication = _repository(client).publish(snapshot)

    assert publication.status is KpiLatestPublicationStatus.PUBLISHED
    assert publication.revision == 'rev-new'
    assert client.upsert_calls[0]['item'] == snapshot.as_document()


@pytest.mark.parametrize(
    'current',
    [
        {'id': 'snapshot', 'partition_id': 'kpis'},
        {'id': 'snapshot', 'partition_id': 'kpis', 'manifest': None},
        {'id': 'snapshot', 'partition_id': 'kpis', 'manifest': {}},
        {'id': 'snapshot', 'partition_id': 'kpis', 'manifest': {'revision': ''}},
        {'id': 'snapshot', 'partition_id': 'kpis', 'manifest': {'revision': ' rev-1 '}},
    ],
)
def test_publish_rejects_invalid_existing_snapshot_without_overwriting(
    current: dict[str, object],
) -> None:
    client = FakeCosmosClient(current=current)

    with pytest.raises(KpiDeliveryRepositoryError):
        _repository(client).publish(_snapshot())

    assert client.upsert_calls == []


def test_publish_rejects_non_latest_identity() -> None:
    client = FakeCosmosClient()
    snapshot = KpiDeliverySnapshot(
        id='snapshot',
        partition_id='time-series',
        manifest=KpiDeliveryManifest(
            schema_version=1,
            updated_at_utc=datetime(2026, 8, 20, 15, 0, tzinfo=UTC),
            revision='series-rev',
        ),
        stores={},
    )

    with pytest.raises(KpiDeliveryRepositoryError, match='partition_id'):
        _repository(client).publish(snapshot)

    assert client.find_calls == []
    assert client.upsert_calls == []


def test_repository_requires_clean_container_name() -> None:
    client = FakeCosmosClient()

    with pytest.raises(KpiDeliveryRepositoryError):
        KpiLatestSnapshotRepository(client=client, container_name='')
    with pytest.raises(KpiDeliveryRepositoryError):
        KpiLatestSnapshotRepository(client=client, container_name=' application-data ')
