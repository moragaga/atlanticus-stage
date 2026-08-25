from datetime import UTC, datetime

import pytest

from ada.kpis.core import KpiWatermark
from ada.kpis.delivery import KpiDeliveryConfiguration, project_kpi_latest
from ada.processes.kpis_delivery import (
    KPI_CONFIGURATION_CONTAINER_NAME,
    KPI_LATEST_DELIVERY_CONTAINER_NAME,
    KpiDeliveryCheckpoint,
    KpiDeliveryConfigurationRepository,
    KpiDeliveryRepositoryError,
    KpiLatestDeliveryCheckpointStore,
    KpiLatestPublicationStatus,
    KpiLatestSnapshotRepository,
)
from atlanticus.state import AtomicStateStore


class _Cosmos:
    def __init__(self, configuration=None, current=None) -> None:
        self.configuration = configuration
        self.current = current
        self.calls = []

    def find_item(self, *, container_name, item_id, partition_key):
        self.calls.append(('find', container_name, item_id, partition_key))
        if container_name == KPI_CONFIGURATION_CONTAINER_NAME:
            return self.configuration
        return self.current

    def upsert_item(self, *, container_name, item):
        self.calls.append(('upsert', container_name, item))
        self.current = item
        return item


def _configuration_document() -> dict[str, object]:
    return {
        'id': 'kpis',
        'partition_key': 'kpis',
        'document_type': 'ada_kpi_configuration_projection',
        'schema_version': 1,
        'revision': 'config-1',
        'tool_projection_revision': 'tools-1',
        'configuration': {'bindings': []},
    }


def test_configuration_repository_uses_internal_configuration_container() -> None:
    client = _Cosmos(configuration=_configuration_document())

    resolved = KpiDeliveryConfigurationRepository(client=client).read()

    assert resolved.revision == 'config-1'
    assert client.calls == [('find', 'configuration', 'kpis', 'kpis')]


def test_checkpoint_roundtrip_and_rejects_watermark_reset(tmp_path) -> None:
    store = KpiLatestDeliveryCheckpointStore(
        store=AtomicStateStore(volume_path=tmp_path, application='ada')
    )
    checkpoint = KpiDeliveryCheckpoint(
        KpiWatermark(datetime(2026, 8, 25, 10, 0, tzinfo=UTC)),
        'config-1',
    )
    store.commit(checkpoint)

    assert store.read() == checkpoint
    with pytest.raises(KpiDeliveryRepositoryError, match='must not regress'):
        store.commit(KpiDeliveryCheckpoint(None, 'config-2'))


def test_repository_uses_internal_latest_container_and_revision_idempotency() -> None:
    configuration = KpiDeliveryConfiguration.from_document(_configuration_document())
    snapshot = project_kpi_latest(
        evaluation=None,
        configuration=configuration,
        watermark=None,
        published_at_utc=datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
    )
    client = _Cosmos()
    repository = KpiLatestSnapshotRepository(client=client)

    first = repository.publish(snapshot)
    second = repository.publish(snapshot)

    assert first.status is KpiLatestPublicationStatus.PUBLISHED
    assert second.status is KpiLatestPublicationStatus.UNCHANGED
    assert any(
        call[0] == 'upsert' and call[1] == KPI_LATEST_DELIVERY_CONTAINER_NAME
        for call in client.calls
    )
