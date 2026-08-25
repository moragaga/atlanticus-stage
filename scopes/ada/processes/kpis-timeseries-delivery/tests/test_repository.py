import json
from datetime import UTC, datetime, timedelta

from ada.kpis.core import KpiWatermark
from ada.kpis.delivery import (
    KpiDeliveryConfiguration,
    KpiTimeseriesPoint,
    project_kpi_timeseries,
)
from ada.processes.kpis_timeseries_delivery import (
    KPI_TIMESERIES_DELIVERY_CONTAINER_NAME,
    KpiTimeseriesPublicationStatus,
    KpiTimeseriesSnapshotRepository,
)


class _Cosmos:
    def __init__(self) -> None:
        self.current = None
        self.upserts: list[tuple[str, dict[str, object]]] = []

    def find_item(self, *, container_name, item_id, partition_key):
        return self.current

    def upsert_item(self, *, container_name, item):
        self.upserts.append((container_name, item))
        if item['id'] == 'timeseries':
            self.current = item
        return item


def _configuration(count: int = 3) -> KpiDeliveryConfiguration:
    return KpiDeliveryConfiguration.from_document(
        {
            'id': 'kpis',
            'partition_key': 'kpis',
            'document_type': 'ada_kpi_configuration_projection',
            'schema_version': 1,
            'revision': 'config-1',
            'tool_projection_revision': 'tools-1',
            'configuration': {
                'bindings': [
                    {
                        'key': f'kpi-{index}',
                        'destination_keys': ['global'],
                        'latest_enabled': False,
                        'series_enabled': True,
                        'series_hours': 24,
                    }
                    for index in range(count)
                ]
            },
        }
    )


def _size(document: dict[str, object]) -> int:
    return len(
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(',', ':'),
        ).encode('utf-8')
    )


def test_repository_publishes_data_before_manifest_and_reuses_revision() -> None:
    end = KpiWatermark(datetime(2026, 8, 25, 12, 0, tzinfo=UTC))
    snapshot = project_kpi_timeseries(
        points=(),
        configuration=_configuration(),
        end_watermark=end,
        step_seconds=120,
        published_at_utc=datetime(2026, 8, 25, 12, 0, 1, tzinfo=UTC),
    )
    client = _Cosmos()
    repository = KpiTimeseriesSnapshotRepository(client=client)

    first = repository.publish(snapshot)
    second = repository.publish(snapshot)

    assert first.status is KpiTimeseriesPublicationStatus.PUBLISHED
    assert second.status is KpiTimeseriesPublicationStatus.UNCHANGED
    assert client.upserts[-1][1]['id'] == 'timeseries'
    assert all(
        container == KPI_TIMESERIES_DELIVERY_CONTAINER_NAME for container, _ in client.upserts
    )
    manifest = client.upserts[-1][1]
    assert manifest['step_seconds'] == 120
    assert manifest['windows'][0]['hours'] == 24
    assert len(manifest['windows'][0]['document_ids']) >= 1
    for _, document in client.upserts:
        assert _size(document) <= 1_800_000


def test_repository_splits_large_series_before_cosmos_limit() -> None:
    end = KpiWatermark(datetime(2026, 8, 25, 12, 0, tzinfo=UTC))
    configuration = _configuration(count=2)
    start = end.timestamp_utc - timedelta(hours=24)
    value = 'x' * 1400
    points = tuple(
        KpiTimeseriesPoint(
            timestamp_utc=start + timedelta(seconds=120 * index),
            key=key,
            value=value,
        )
        for key in ('kpi-0', 'kpi-1')
        for index in range(1, 721)
    )
    snapshot = project_kpi_timeseries(
        points=points,
        configuration=configuration,
        end_watermark=end,
        step_seconds=120,
        published_at_utc=datetime(2026, 8, 25, 12, 0, 1, tzinfo=UTC),
    )
    client = _Cosmos()

    KpiTimeseriesSnapshotRepository(client=client).publish(snapshot)

    data_documents = [document for _, document in client.upserts if document['id'] != 'timeseries']
    assert len(data_documents) == 2
    assert all(_size(document) <= 1_800_000 for document in data_documents)
