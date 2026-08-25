from datetime import UTC, datetime

import pytest

from ada.kpis.core import KpiValueKind, KpiWatermark
from ada.kpis.delivery import (
    KpiDeliveryManifest,
    KpiDeliverySnapshot,
    KpiDeliveryStatus,
    KpiDeliveryValidationError,
    KpiDeliveryValue,
)


def _manifest() -> KpiDeliveryManifest:
    return KpiDeliveryManifest(
        schema_version=1,
        watermark=KpiWatermark(datetime(2026, 8, 25, 10, 0, tzinfo=UTC)),
        configuration_revision='config-1',
        tool_projection_revision='tools-1',
        published_at_utc=datetime(2026, 8, 25, 10, 0, 1, tzinfo=UTC),
        revision='0123456789abcdef',
    )


def test_missing_contract_requires_null_kind_and_value() -> None:
    with pytest.raises(KpiDeliveryValidationError, match='missing value'):
        KpiDeliveryValue(
            status=KpiDeliveryStatus.MISSING,
            value_kind=KpiValueKind.VALUE,
            value=None,
        )


def test_error_contract_requires_value_kind_and_null_value() -> None:
    with pytest.raises(KpiDeliveryValidationError, match='error value'):
        KpiDeliveryValue(
            status=KpiDeliveryStatus.ERROR,
            value_kind=KpiValueKind.VALUE,
            value='unsafe',
        )


def test_snapshot_defensively_copies_destination_mappings() -> None:
    values = {
        'kpi': KpiDeliveryValue(
            status=KpiDeliveryStatus.OK,
            value_kind=KpiValueKind.VALUE,
            value='10',
        )
    }
    destinations = {'global': values}
    snapshot = KpiDeliverySnapshot(
        id='latest',
        partition_id='kpis',
        document_type='ada_kpi_latest_delivery',
        manifest=_manifest(),
        destinations=destinations,
    )

    values.clear()
    destinations.clear()

    assert tuple(snapshot.destinations) == ('global',)
    assert tuple(snapshot.destinations['global']) == ('kpi',)
