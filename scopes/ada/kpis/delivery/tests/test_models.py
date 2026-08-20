from datetime import UTC, datetime

import pytest

from ada.kpis.core import KpiValueKind
from ada.kpis.delivery import (
    KpiDeliveryBinding,
    KpiDeliveryManifest,
    KpiDeliverySnapshot,
    KpiDeliveryStatus,
    KpiDeliveryValidationError,
    KpiDeliveryValue,
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


def test_binding_rejects_blank_identifiers() -> None:
    with pytest.raises(KpiDeliveryValidationError, match='store_key'):
        KpiDeliveryBinding(store_key='', kpi_key='kpi')


def test_snapshot_defensively_copies_store_mappings() -> None:
    values = {
        'kpi': KpiDeliveryValue(
            status=KpiDeliveryStatus.OK,
            value_kind=KpiValueKind.VALUE,
            value='10',
        )
    }
    stores = {'store': values}
    snapshot = KpiDeliverySnapshot(
        id='snapshot',
        partition_id='kpis',
        manifest=KpiDeliveryManifest(
            schema_version=1,
            updated_at_utc=datetime(2026, 8, 20, 14, 30, tzinfo=UTC),
            revision='0123456789abcdef',
        ),
        stores=stores,
    )

    values.clear()
    stores.clear()

    assert tuple(snapshot.stores) == ('store',)
    assert tuple(snapshot.stores['store']) == ('kpi',)
