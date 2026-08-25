# Proceso Latest: congela configuración por job, observa watermark fresco y publica sólo cuando corresponde.
# Coordina una iteración: gate por checkpoint, trabajo y commit posterior.

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from ada.kpis.delivery import KpiDeliveryConfiguration, KpiDeliveryStatus, project_kpi_latest
from ada.processes.kpis_delivery.contracts import (
    KpiCommittedWatermarkReader,
    KpiDeliveryCheckpointStore,
    KpiDeliveryConfigurationReader,
    KpiLatestReader,
    KpiLatestSnapshotPublisher,
)
from ada.processes.kpis_delivery.errors import KpiDeliveryRepositoryError
from ada.processes.kpis_delivery.models import (
    KpiDeliveryCheckpoint,
    KpiLatestPublication,
    KpiLatestPublicationStatus,
)
from atlanticus.runtime import JobRuntimeContext


# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiLatestDeliveryIterationStatus(StrEnum):
    PUBLISHED = 'published'
    UNCHANGED = 'unchanged'
    SKIPPED_CURRENT = 'skipped_current'


@dataclass(frozen=True, slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiLatestDeliveryIterationResult:
    status: KpiLatestDeliveryIterationStatus
    watermark_utc: str | None
    configuration_revision: str
    delivery_revision: str | None = None
    destination_count: int = 0
    value_count: int = 0
    missing_count: int = 0
    error_count: int = 0


# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiLatestDeliveryJob:
    def __init__(
        self,
        *,
        configuration: KpiDeliveryConfigurationReader,
        kpi_state: KpiCommittedWatermarkReader,
        latest: KpiLatestReader,
        checkpoint: KpiDeliveryCheckpointStore,
        snapshots: KpiLatestSnapshotPublisher,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(configuration, KpiDeliveryConfigurationReader):
            raise TypeError('configuration must implement KpiDeliveryConfigurationReader')
        if not isinstance(kpi_state, KpiCommittedWatermarkReader):
            raise TypeError('kpi_state must implement KpiCommittedWatermarkReader')
        if not isinstance(latest, KpiLatestReader):
            raise TypeError('latest must implement KpiLatestReader')
        if not isinstance(checkpoint, KpiDeliveryCheckpointStore):
            raise TypeError('checkpoint must implement KpiDeliveryCheckpointStore')
        if not isinstance(snapshots, KpiLatestSnapshotPublisher):
            raise TypeError('snapshots must implement KpiLatestSnapshotPublisher')
        if now is not None and not callable(now):
            raise TypeError('now must be callable or None')
        self._configuration_reader = configuration
        self._kpi_state = kpi_state
        self._latest = latest
        self._checkpoint_store = checkpoint
        self._snapshots = snapshots
        self._now = now or _utc_now
        self._configuration: KpiDeliveryConfiguration | None = None
        self._checkpoint: KpiDeliveryCheckpoint | None = None

    def run_iteration(self, context: JobRuntimeContext) -> KpiLatestDeliveryIterationResult:
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be a JobRuntimeContext')
        context.raise_if_cancelled()
        configuration = self._frozen_configuration()
        checkpoint = self._current_checkpoint()
        watermark = self._kpi_state.read_watermark()
        context.raise_if_cancelled()
        if checkpoint is not None and checkpoint.watermark is not None and watermark is None:
            raise KpiDeliveryRepositoryError(
                'KPI committed watermark is missing after delivery progress'
            )
        # Rechaza una regresión antes de leer Latest o publicar una fotografía antigua.
        if (
            checkpoint is not None
            and checkpoint.watermark is not None
            and watermark is not None
            and watermark < checkpoint.watermark
        ):
            raise KpiDeliveryRepositoryError(
                'KPI committed watermark must not regress behind delivery checkpoint'
            )
        if _is_current(
            checkpoint=checkpoint,
            watermark=watermark,
            configuration_revision=configuration.revision,
        ):
            return _record_result(
                context,
                KpiLatestDeliveryIterationResult(
                    status=KpiLatestDeliveryIterationStatus.SKIPPED_CURRENT,
                    watermark_utc=None if watermark is None else watermark.text,
                    configuration_revision=configuration.revision,
                ),
            )
        latest = self._latest.read() if watermark is not None else None
        if watermark is not None:
            if latest is None:
                raise KpiDeliveryRepositoryError(
                    'KPI latest evaluation is missing for the committed watermark'
                )
            if latest.watermark != watermark:
                raise KpiDeliveryRepositoryError(
                    'KPI latest evaluation does not match the committed watermark'
                )
        context.raise_if_cancelled()
        snapshot = project_kpi_latest(
            evaluation=latest,
            configuration=configuration,
            watermark=watermark,
            published_at_utc=self._now(),
        )
        context.raise_if_cancelled()
        publication = self._snapshots.publish(snapshot)
        context.raise_if_cancelled()
        committed = self._checkpoint_store.commit(
            KpiDeliveryCheckpoint(
                watermark=watermark,
                configuration_revision=configuration.revision,
            )
        )
        self._checkpoint = committed
        return _record_result(
            context,
            _result(publication=publication, snapshot=snapshot),
        )

    def _frozen_configuration(self) -> KpiDeliveryConfiguration:
        if self._configuration is None:
            self._configuration = self._configuration_reader.read()
        return self._configuration

    def _current_checkpoint(self) -> KpiDeliveryCheckpoint | None:
        if self._checkpoint is None:
            self._checkpoint = self._checkpoint_store.read()
        return self._checkpoint


# La función mantiene una operación pequeña y verificable de esta frontera.
def _is_current(
    *,
    checkpoint: KpiDeliveryCheckpoint | None,
    watermark,
    configuration_revision: str,
) -> bool:
    return (
        checkpoint is not None
        and checkpoint.watermark == watermark
        and checkpoint.configuration_revision == configuration_revision
    )


# La función mantiene una operación pequeña y verificable de esta frontera.
def _result(
    *,
    publication: KpiLatestPublication,
    snapshot,
) -> KpiLatestDeliveryIterationResult:
    values = tuple(
        value for destination in snapshot.destinations.values() for value in destination.values()
    )
    status = (
        KpiLatestDeliveryIterationStatus.PUBLISHED
        if publication.status is KpiLatestPublicationStatus.PUBLISHED
        else KpiLatestDeliveryIterationStatus.UNCHANGED
    )
    return KpiLatestDeliveryIterationResult(
        status=status,
        watermark_utc=(
            None if snapshot.manifest.watermark is None else snapshot.manifest.watermark.text
        ),
        configuration_revision=snapshot.manifest.configuration_revision,
        delivery_revision=publication.revision,
        destination_count=len(snapshot.destinations),
        value_count=len(values),
        missing_count=sum(value.status is KpiDeliveryStatus.MISSING for value in values),
        error_count=sum(value.status is KpiDeliveryStatus.ERROR for value in values),
    )


# La función mantiene una operación pequeña y verificable de esta frontera.
def _record_result(
    context: JobRuntimeContext,
    result: KpiLatestDeliveryIterationResult,
) -> KpiLatestDeliveryIterationResult:
    skipped = result.status is KpiLatestDeliveryIterationStatus.SKIPPED_CURRENT
    context.set_iteration_fact('outcome', 'skipped' if skipped else 'completed')
    context.set_iteration_fact('reason', result.status.value)
    context.set_iteration_fact('configuration_revision', result.configuration_revision)
    if result.watermark_utc is not None:
        context.set_iteration_fact('watermark_utc', result.watermark_utc)
    if result.delivery_revision is not None:
        context.set_iteration_fact('delivery_revision', result.delivery_revision)
    context.set_iteration_fact('destination_count', result.destination_count)
    context.set_iteration_fact('value_count', result.value_count)
    context.set_iteration_fact('missing_count', result.missing_count)
    context.set_iteration_fact('error_count', result.error_count)
    if not skipped:
        context.mark_iteration_work()
        if result.status is KpiLatestDeliveryIterationStatus.PUBLISHED:
            context.increment_execution_counter('snapshots_published')
    return result


# La función mantiene una operación pequeña y verificable de esta frontera.
def _utc_now() -> datetime:
    return datetime.now(UTC)
