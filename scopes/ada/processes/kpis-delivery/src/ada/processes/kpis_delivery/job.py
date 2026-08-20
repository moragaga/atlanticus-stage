from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from ada.kpis.delivery import KpiDeliveryStatus, KpiDeliveryValue, project_kpi_latest
from ada.processes.kpis_delivery.contracts import (
    KpiDeliveryBindingsReader,
    KpiLatestReader,
    KpiLatestSnapshotPublisher,
)
from ada.processes.kpis_delivery.models import (
    KpiLatestPublication,
    KpiLatestPublicationStatus,
)
from atlanticus.runtime import JobRuntimeContext


@dataclass(frozen=True, slots=True)
class KpiLatestDeliveryIterationResult:
    publication: KpiLatestPublication
    store_count: int
    value_count: int
    missing_count: int
    error_count: int


class KpiLatestDeliveryJob:
    def __init__(
        self,
        *,
        latest: KpiLatestReader,
        bindings: KpiDeliveryBindingsReader,
        snapshots: KpiLatestSnapshotPublisher,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(latest, KpiLatestReader):
            raise TypeError('latest must implement KpiLatestReader')
        if not isinstance(bindings, KpiDeliveryBindingsReader):
            raise TypeError('bindings must implement KpiDeliveryBindingsReader')
        if not isinstance(snapshots, KpiLatestSnapshotPublisher):
            raise TypeError('snapshots must implement KpiLatestSnapshotPublisher')
        if now is not None and not callable(now):
            raise TypeError('now must be callable or None')
        self._latest = latest
        self._bindings = bindings
        self._snapshots = snapshots
        self._now = now or _utc_now

    def run_iteration(self, context: JobRuntimeContext) -> KpiLatestDeliveryIterationResult:
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be a JobRuntimeContext')
        context.raise_if_cancelled()
        bindings = self._bindings.read_bindings()
        context.raise_if_cancelled()
        latest = None if not bindings else self._latest.read()
        context.raise_if_cancelled()
        snapshot = project_kpi_latest(
            evaluation=latest,
            bindings=bindings,
            updated_at_utc=self._now(),
        )
        context.raise_if_cancelled()
        publication = self._snapshots.publish(snapshot)
        result = _result(
            publication=publication,
            stores=snapshot.stores,
        )
        return _record_result(context, result)


def _result(
    *,
    publication: KpiLatestPublication,
    stores: Mapping[str, Mapping[str, KpiDeliveryValue]],
) -> KpiLatestDeliveryIterationResult:
    values = tuple(value for store in stores.values() for value in store.values())
    return KpiLatestDeliveryIterationResult(
        publication=publication,
        store_count=len(stores),
        value_count=len(values),
        missing_count=sum(value.status is KpiDeliveryStatus.MISSING for value in values),
        error_count=sum(value.status is KpiDeliveryStatus.ERROR for value in values),
    )


def _record_result(
    context: JobRuntimeContext,
    result: KpiLatestDeliveryIterationResult,
) -> KpiLatestDeliveryIterationResult:
    published = result.publication.status is KpiLatestPublicationStatus.PUBLISHED
    context.set_iteration_fact('outcome', 'completed' if published else 'skipped')
    context.set_iteration_fact('reason', result.publication.status.value)
    context.set_iteration_fact('delivery_revision', result.publication.revision)
    context.set_iteration_fact('store_count', result.store_count)
    context.set_iteration_fact('value_count', result.value_count)
    context.set_iteration_fact('missing_count', result.missing_count)
    context.set_iteration_fact('error_count', result.error_count)
    if published:
        context.mark_iteration_work()
        context.increment_execution_counter('snapshots_published')
    return result


def _utc_now() -> datetime:
    return datetime.now(UTC)
