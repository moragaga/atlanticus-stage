# Proyección pura: convierte el latest de Evaluation en stores consumibles sin conocer Cosmos ni configuración física.
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime

from ada.kpis.core import KpiEvaluation, KpiStatus, KpiValueKind
from ada.kpis.delivery.models import (
    KpiDeliveryBinding,
    KpiDeliveryManifest,
    KpiDeliverySnapshot,
    KpiDeliveryStatus,
    KpiDeliveryValue,
)

KPI_LATEST_DELIVERY_ID = 'snapshot'
KPI_LATEST_PARTITION_ID = 'kpis'
KPI_LATEST_SCHEMA_VERSION = 1


def project_kpi_latest(
    *,
    evaluation: KpiEvaluation,
    bindings: Iterable[KpiDeliveryBinding],
    updated_at_utc: datetime,
) -> KpiDeliverySnapshot:
    if not isinstance(evaluation, KpiEvaluation):
        raise TypeError('evaluation must be KpiEvaluation')
    normalized_bindings = _normalize_bindings(bindings)
    results = {result.key: result for result in evaluation.results}
    stores: dict[str, dict[str, KpiDeliveryValue]] = {}
    for binding in normalized_bindings:
        store = stores.setdefault(binding.store_key, {})
        result = results.get(binding.kpi_key)
        if result is None:
            store[binding.kpi_key] = KpiDeliveryValue(
                status=KpiDeliveryStatus.MISSING,
                value_kind=None,
                value=None,
            )
            continue
        if result.status is KpiStatus.ERROR:
            store[binding.kpi_key] = KpiDeliveryValue(
                status=KpiDeliveryStatus.ERROR,
                value_kind=result.value_kind,
                value=None,
            )
            continue
        store[binding.kpi_key] = KpiDeliveryValue(
            status=KpiDeliveryStatus.OK,
            value_kind=result.value_kind,
            value=result.parsed_value if result.value_kind is KpiValueKind.VALUE else result.value,
        )
    revision = calculate_kpi_latest_revision(stores)
    return KpiDeliverySnapshot(
        id=KPI_LATEST_DELIVERY_ID,
        partition_id=KPI_LATEST_PARTITION_ID,
        manifest=KpiDeliveryManifest(
            schema_version=KPI_LATEST_SCHEMA_VERSION,
            updated_at_utc=updated_at_utc,
            revision=revision,
        ),
        stores=stores,
    )


def calculate_kpi_latest_revision(
    stores: Mapping[str, Mapping[str, KpiDeliveryValue]],
) -> str:
    payload = {
        'schema_version': KPI_LATEST_SCHEMA_VERSION,
        'stores': {
            store_key: {
                kpi_key: value.as_payload() for kpi_key, value in values.items()
            }
            for store_key, values in stores.items()
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()[:16]


def _normalize_bindings(bindings: Iterable[KpiDeliveryBinding]) -> tuple[KpiDeliveryBinding, ...]:
    try:
        values = tuple(bindings)
    except TypeError as error:
        raise TypeError('bindings must be iterable') from error
    if not all(isinstance(binding, KpiDeliveryBinding) for binding in values):
        raise TypeError('bindings must contain KpiDeliveryBinding values')
    return tuple(dict.fromkeys(values))
