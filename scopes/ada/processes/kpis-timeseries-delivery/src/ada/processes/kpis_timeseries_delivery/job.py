from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from ada.kpis.core import KpiWatermark
from ada.kpis.delivery import KpiDeliveryConfiguration, project_kpi_timeseries
from ada.processes.kpis_timeseries_delivery.contracts import (
    KpiHistorianWatermarkReader,
    KpiTimeseriesCheckpointStore,
    KpiTimeseriesConfigurationReader,
    KpiTimeseriesHistoryReader,
    KpiTimeseriesSnapshotPublisher,
)
from ada.processes.kpis_timeseries_delivery.errors import (
    KpiTimeseriesDeliveryRepositoryError,
)
from ada.processes.kpis_timeseries_delivery.models import (
    KpiTimeseriesCheckpoint,
    KpiTimeseriesPublicationStatus,
)
from atlanticus.runtime import JobRuntimeContext

KPI_TIMESERIES_STEP_SECONDS = 120


class KpiTimeseriesDeliveryIterationStatus(StrEnum):
    PUBLISHED = 'published'
    UNCHANGED = 'unchanged'
    SKIPPED_CURRENT = 'skipped_current'
    SKIPPED_NO_WATERMARK = 'skipped_no_watermark'


@dataclass(frozen=True, slots=True)
class KpiTimeseriesDeliveryIterationResult:
    status: KpiTimeseriesDeliveryIterationStatus
    watermark_utc: str | None
    configuration_revision: str
    delivery_revision: str | None = None
    document_count: int = 0
    window_count: int = 0
    kpi_count: int = 0


class KpiTimeseriesDeliveryJob:
    def __init__(
        self,
        *,
        configuration: KpiTimeseriesConfigurationReader,
        historian_state: KpiHistorianWatermarkReader,
        history: KpiTimeseriesHistoryReader,
        checkpoint: KpiTimeseriesCheckpointStore,
        snapshots: KpiTimeseriesSnapshotPublisher,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(configuration, KpiTimeseriesConfigurationReader):
            raise TypeError('configuration must implement KpiTimeseriesConfigurationReader')
        if not isinstance(historian_state, KpiHistorianWatermarkReader):
            raise TypeError('historian_state must implement KpiHistorianWatermarkReader')
        if not isinstance(history, KpiTimeseriesHistoryReader):
            raise TypeError('history must implement KpiTimeseriesHistoryReader')
        if not isinstance(checkpoint, KpiTimeseriesCheckpointStore):
            raise TypeError('checkpoint must implement KpiTimeseriesCheckpointStore')
        if not isinstance(snapshots, KpiTimeseriesSnapshotPublisher):
            raise TypeError('snapshots must implement KpiTimeseriesSnapshotPublisher')
        if now is not None and not callable(now):
            raise TypeError('now must be callable or None')
        self._configuration_reader = configuration
        self._historian_state = historian_state
        self._history = history
        self._checkpoint_store = checkpoint
        self._snapshots = snapshots
        self._now = now or _utc_now
        self._configuration: KpiDeliveryConfiguration | None = None
        self._checkpoint: KpiTimeseriesCheckpoint | None = None

    def run_iteration(self, context: JobRuntimeContext) -> KpiTimeseriesDeliveryIterationResult:
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be a JobRuntimeContext')
        context.raise_if_cancelled()
        configuration = self._frozen_configuration()
        checkpoint = self._current_checkpoint()
        historian_watermark = self._historian_state.read_watermark()
        context.raise_if_cancelled()
        if historian_watermark is None:
            if checkpoint is not None and checkpoint.watermark is not None:
                raise KpiTimeseriesDeliveryRepositoryError(
                    'KPI historian committed watermark is missing after delivery progress'
                )
            return _record_result(
                context,
                KpiTimeseriesDeliveryIterationResult(
                    status=KpiTimeseriesDeliveryIterationStatus.SKIPPED_NO_WATERMARK,
                    watermark_utc=None,
                    configuration_revision=configuration.revision,
                ),
            )
        end_watermark = _align_watermark(
            historian_watermark,
            step_seconds=KPI_TIMESERIES_STEP_SECONDS,
        )
        if (
            checkpoint is not None
            and checkpoint.watermark is not None
            and end_watermark < checkpoint.watermark
        ):
            raise KpiTimeseriesDeliveryRepositoryError(
                'KPI historian committed watermark must not regress behind delivery checkpoint'
            )
        if _is_current(
            checkpoint=checkpoint,
            watermark=end_watermark,
            configuration_revision=configuration.revision,
        ):
            return _record_result(
                context,
                KpiTimeseriesDeliveryIterationResult(
                    status=KpiTimeseriesDeliveryIterationStatus.SKIPPED_CURRENT,
                    watermark_utc=end_watermark.text,
                    configuration_revision=configuration.revision,
                ),
            )
        series_bindings = configuration.series_bindings
        max_hours = max(
            (
                binding.series_hours
                for binding in series_bindings
                if binding.series_hours is not None
            ),
            default=0,
        )
        keys = tuple(binding.key for binding in series_bindings)
        points = ()
        if max_hours > 0:
            points = self._history.read_points(
                keys=keys,
                start_utc=end_watermark.timestamp_utc - timedelta(hours=max_hours),
                end_utc=end_watermark.timestamp_utc,
                step_seconds=KPI_TIMESERIES_STEP_SECONDS,
            )
        context.raise_if_cancelled()
        snapshot = project_kpi_timeseries(
            points=points,
            configuration=configuration,
            end_watermark=end_watermark,
            step_seconds=KPI_TIMESERIES_STEP_SECONDS,
            published_at_utc=self._now(),
        )
        context.raise_if_cancelled()
        publication = self._snapshots.publish(snapshot)
        context.raise_if_cancelled()
        self._commit_checkpoint(
            KpiTimeseriesCheckpoint(
                watermark=end_watermark,
                configuration_revision=configuration.revision,
            )
        )
        status = (
            KpiTimeseriesDeliveryIterationStatus.PUBLISHED
            if publication.status is KpiTimeseriesPublicationStatus.PUBLISHED
            else KpiTimeseriesDeliveryIterationStatus.UNCHANGED
        )
        return _record_result(
            context,
            KpiTimeseriesDeliveryIterationResult(
                status=status,
                watermark_utc=end_watermark.text,
                configuration_revision=configuration.revision,
                delivery_revision=publication.revision,
                document_count=publication.document_count,
                window_count=len(snapshot.windows),
                kpi_count=len(series_bindings),
            ),
        )

    def _frozen_configuration(self) -> KpiDeliveryConfiguration:
        if self._configuration is None:
            self._configuration = self._configuration_reader.read()
        return self._configuration

    def _current_checkpoint(self) -> KpiTimeseriesCheckpoint | None:
        if self._checkpoint is None:
            self._checkpoint = self._checkpoint_store.read()
        return self._checkpoint

    def _commit_checkpoint(self, checkpoint: KpiTimeseriesCheckpoint) -> None:
        self._checkpoint = self._checkpoint_store.commit(checkpoint)


def _align_watermark(watermark: KpiWatermark, *, step_seconds: int) -> KpiWatermark:
    timestamp = watermark.timestamp_utc
    epoch_seconds = int(timestamp.timestamp())
    aligned_seconds = epoch_seconds - epoch_seconds % step_seconds
    return KpiWatermark(datetime.fromtimestamp(aligned_seconds, tz=UTC))


def _is_current(
    *,
    checkpoint: KpiTimeseriesCheckpoint | None,
    watermark: KpiWatermark,
    configuration_revision: str,
) -> bool:
    return (
        checkpoint is not None
        and checkpoint.watermark == watermark
        and checkpoint.configuration_revision == configuration_revision
    )


def _record_result(
    context: JobRuntimeContext,
    result: KpiTimeseriesDeliveryIterationResult,
) -> KpiTimeseriesDeliveryIterationResult:
    skipped = result.status in {
        KpiTimeseriesDeliveryIterationStatus.SKIPPED_CURRENT,
        KpiTimeseriesDeliveryIterationStatus.SKIPPED_NO_WATERMARK,
    }
    context.set_iteration_fact('outcome', 'skipped' if skipped else 'completed')
    context.set_iteration_fact('reason', result.status.value)
    context.set_iteration_fact('configuration_revision', result.configuration_revision)
    if result.watermark_utc is not None:
        context.set_iteration_fact('watermark_utc', result.watermark_utc)
    if result.delivery_revision is not None:
        context.set_iteration_fact('delivery_revision', result.delivery_revision)
    context.set_iteration_fact('document_count', result.document_count)
    context.set_iteration_fact('window_count', result.window_count)
    context.set_iteration_fact('kpi_count', result.kpi_count)
    if not skipped:
        context.mark_iteration_work()
        if result.status is KpiTimeseriesDeliveryIterationStatus.PUBLISHED:
            context.increment_execution_counter('snapshots_published')
    return result


def _utc_now() -> datetime:
    return datetime.now(UTC)
