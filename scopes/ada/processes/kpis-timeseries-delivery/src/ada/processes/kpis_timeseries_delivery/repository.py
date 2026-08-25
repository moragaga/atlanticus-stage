from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ada.kpis.delivery import (
    KPI_TIMESERIES_DELIVERY_ID,
    KPI_TIMESERIES_PARTITION_ID,
    KpiTimeseriesSnapshot,
)
from ada.processes.kpis_timeseries_delivery.errors import KpiTimeseriesDeliveryRepositoryError
from ada.processes.kpis_timeseries_delivery.models import (
    KpiTimeseriesPublication,
    KpiTimeseriesPublicationStatus,
)
from atlanticus.connectivity.cosmos import CosmosClient

KPI_TIMESERIES_DELIVERY_CONTAINER_NAME = 'kpis-timeseries-delivery'
KPI_TIMESERIES_DATA_DOCUMENT_TYPE = 'ada_kpi_timeseries_delivery_data'
_MAX_DOCUMENT_BYTES = 1_800_000


@dataclass(slots=True)
class KpiTimeseriesSnapshotRepository:
    client: CosmosClient

    def publish(self, snapshot: KpiTimeseriesSnapshot) -> KpiTimeseriesPublication:
        self._validate_snapshot(snapshot)
        current = self.client.find_item(
            container_name=KPI_TIMESERIES_DELIVERY_CONTAINER_NAME,
            item_id=snapshot.id,
            partition_key=snapshot.partition_id,
        )
        if current is not None and self._current_revision(current) == snapshot.manifest.revision:
            return KpiTimeseriesPublication(
                status=KpiTimeseriesPublicationStatus.UNCHANGED,
                revision=snapshot.manifest.revision,
                document_count=0,
            )
        pages, windows = _build_pages(snapshot)
        for page in pages:
            self.client.upsert_item(
                container_name=KPI_TIMESERIES_DELIVERY_CONTAINER_NAME,
                item=page,
            )
        manifest = _manifest_document(snapshot=snapshot, windows=windows)
        _require_document_size(manifest)
        self.client.upsert_item(
            container_name=KPI_TIMESERIES_DELIVERY_CONTAINER_NAME,
            item=manifest,
        )
        return KpiTimeseriesPublication(
            status=KpiTimeseriesPublicationStatus.PUBLISHED,
            revision=snapshot.manifest.revision,
            document_count=len(pages) + 1,
        )

    @staticmethod
    def _validate_snapshot(snapshot: KpiTimeseriesSnapshot) -> None:
        if not isinstance(snapshot, KpiTimeseriesSnapshot):
            raise TypeError('snapshot must be KpiTimeseriesSnapshot')
        if snapshot.id != KPI_TIMESERIES_DELIVERY_ID:
            raise KpiTimeseriesDeliveryRepositoryError(
                'snapshot id is not valid for KPI timeseries delivery'
            )
        if snapshot.partition_id != KPI_TIMESERIES_PARTITION_ID:
            raise KpiTimeseriesDeliveryRepositoryError(
                'snapshot partition_id is not valid for KPI timeseries delivery'
            )

    @staticmethod
    def _current_revision(document: dict[str, Any]) -> str:
        manifest = document.get('manifest')
        if not isinstance(manifest, dict):
            raise KpiTimeseriesDeliveryRepositoryError(
                'existing KPI timeseries manifest is invalid'
            )
        revision = manifest.get('revision')
        if not isinstance(revision, str) or not revision or revision != revision.strip():
            raise KpiTimeseriesDeliveryRepositoryError(
                'existing KPI timeseries revision is invalid'
            )
        return revision


def _build_pages(
    snapshot: KpiTimeseriesSnapshot,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    pages: list[dict[str, object]] = []
    windows: list[dict[str, object]] = []
    for window in snapshot.windows:
        page_ids: list[str] = []
        current_keys: list[str] = []
        current_values: list[list[object]] = []
        page_index = 0
        for key, row in zip(window.keys, window.values, strict=True):
            candidate_keys = [*current_keys, key]
            candidate_values = [*current_values, list(row)]
            candidate = _page_document(
                snapshot=snapshot,
                hours=window.hours,
                page_index=page_index,
                keys=candidate_keys,
                values=candidate_values,
            )
            if _document_size(candidate) > _MAX_DOCUMENT_BYTES and current_keys:
                page = _page_document(
                    snapshot=snapshot,
                    hours=window.hours,
                    page_index=page_index,
                    keys=current_keys,
                    values=current_values,
                )
                _require_document_size(page)
                pages.append(page)
                page_ids.append(str(page['id']))
                page_index += 1
                current_keys = [key]
                current_values = [list(row)]
                single = _page_document(
                    snapshot=snapshot,
                    hours=window.hours,
                    page_index=page_index,
                    keys=current_keys,
                    values=current_values,
                )
                _require_document_size(single)
            else:
                current_keys = candidate_keys
                current_values = candidate_values
                _require_document_size(candidate)
        if current_keys:
            page = _page_document(
                snapshot=snapshot,
                hours=window.hours,
                page_index=page_index,
                keys=current_keys,
                values=current_values,
            )
            _require_document_size(page)
            pages.append(page)
            page_ids.append(str(page['id']))
        windows.append(
            {
                'hours': window.hours,
                'start_utc': _format_utc(window.start_utc),
                'document_ids': page_ids,
            }
        )
    return pages, windows


def _page_document(
    *,
    snapshot: KpiTimeseriesSnapshot,
    hours: int,
    page_index: int,
    keys: list[str],
    values: list[list[object]],
) -> dict[str, object]:
    return {
        'id': f'timeseries-{hours:02d}-{page_index:03d}',
        'partition_id': snapshot.partition_id,
        'document_type': KPI_TIMESERIES_DATA_DOCUMENT_TYPE,
        'revision': snapshot.manifest.revision,
        'keys': keys,
        'values': values,
    }


def _manifest_document(
    *,
    snapshot: KpiTimeseriesSnapshot,
    windows: list[dict[str, object]],
) -> dict[str, object]:
    return {
        'id': snapshot.id,
        'partition_id': snapshot.partition_id,
        'document_type': snapshot.document_type,
        'manifest': snapshot.manifest.as_payload(),
        'end_utc': _format_utc(snapshot.end_utc),
        'step_seconds': snapshot.step_seconds,
        'destinations': {key: list(values) for key, values in snapshot.destinations.items()},
        'windows': windows,
    }


def _require_document_size(document: dict[str, object]) -> None:
    if _document_size(document) > _MAX_DOCUMENT_BYTES:
        raise KpiTimeseriesDeliveryRepositoryError(
            'KPI timeseries delivery document exceeds the safe Cosmos item size'
        )


def _document_size(document: dict[str, object]) -> int:
    return len(
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(',', ':'),
        ).encode('utf-8')
    )


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec='microseconds').replace('+00:00', 'Z')
