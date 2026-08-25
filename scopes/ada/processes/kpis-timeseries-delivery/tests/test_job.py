from datetime import UTC, datetime

import pytest

from ada.kpis.core import KpiWatermark
from ada.kpis.delivery import KpiDeliveryConfiguration, KpiTimeseriesPoint
from ada.processes.kpis_timeseries_delivery import (
    KPI_TIMESERIES_STEP_SECONDS,
    KpiTimeseriesCheckpoint,
    KpiTimeseriesDeliveryIterationStatus,
    KpiTimeseriesDeliveryJob,
    KpiTimeseriesDeliveryRepositoryError,
    KpiTimeseriesPublication,
    KpiTimeseriesPublicationStatus,
)
from atlanticus.kernel import Environment
from atlanticus.runtime import JobDefinition, JobRuntimeContext, RuntimeConfiguration


def _configuration(revision: str = 'config-1') -> KpiDeliveryConfiguration:
    return KpiDeliveryConfiguration.from_document(
        {
            'id': 'kpis',
            'partition_key': 'kpis',
            'document_type': 'ada_kpi_configuration_projection',
            'schema_version': 1,
            'revision': revision,
            'tool_projection_revision': 'tools-1',
            'configuration': {
                'bindings': [
                    {
                        'key': 'production',
                        'destination_keys': ['global', 'mill'],
                        'latest_enabled': True,
                        'series_enabled': True,
                        'series_hours': 1,
                    }
                ]
            },
        }
    )


class _Configuration:
    def __init__(self, value: KpiDeliveryConfiguration) -> None:
        self.value = value
        self.calls = 0

    def read(self) -> KpiDeliveryConfiguration:
        self.calls += 1
        return self.value


class _Historian:
    def __init__(self, value: KpiWatermark | None) -> None:
        self.value = value

    def read_watermark(self) -> KpiWatermark | None:
        return self.value


class _History:
    def __init__(self, points: tuple[KpiTimeseriesPoint, ...] = ()) -> None:
        self.points = points
        self.calls: list[dict[str, object]] = []

    def read_points(self, **kwargs) -> tuple[KpiTimeseriesPoint, ...]:
        self.calls.append(kwargs)
        return self.points


class _Checkpoint:
    def __init__(self, value: KpiTimeseriesCheckpoint | None = None) -> None:
        self.value = value
        self.commits: list[KpiTimeseriesCheckpoint] = []

    def read(self) -> KpiTimeseriesCheckpoint | None:
        return self.value

    def commit(self, value: KpiTimeseriesCheckpoint) -> KpiTimeseriesCheckpoint:
        self.value = value
        self.commits.append(value)
        return value


class _Publisher:
    def __init__(self) -> None:
        self.snapshots = []

    def publish(self, snapshot):
        self.snapshots.append(snapshot)
        return KpiTimeseriesPublication(
            KpiTimeseriesPublicationStatus.PUBLISHED,
            snapshot.manifest.revision,
            2,
        )


def _context(tmp_path) -> JobRuntimeContext:
    definition = JobDefinition(
        module_name='ada.processes.kpis_timeseries_delivery',
        service_name='kpis-timeseries-delivery',
        job_key='test',
        iteration_timeout_seconds=30,
        execution_timeout_seconds=60,
        shutdown_grace_seconds=5,
        lease_timeout_seconds=30,
        lease_renew_seconds=10,
    )
    runtime = RuntimeConfiguration(
        environment=Environment.from_value('local'),
        application='ada-test',
        volume_path=tmp_path,
    )
    return JobRuntimeContext.create(
        definition=definition,
        configuration=runtime,
        run_id='run-1',
        correlation_id='corr-1',
    )


def test_step_is_initial_internal_configuration() -> None:
    assert KPI_TIMESERIES_STEP_SECONDS == 120


def test_job_aligns_historian_watermark_and_only_reads_exact_delivery_window(tmp_path) -> None:
    historian = KpiWatermark(datetime(2026, 8, 25, 11, 1, 30, tzinfo=UTC))
    point = KpiTimeseriesPoint(
        datetime(2026, 8, 25, 11, 0, tzinfo=UTC),
        'production',
        20.0,
    )
    history = _History((point,))
    checkpoint = _Checkpoint()
    publisher = _Publisher()
    configuration = _Configuration(_configuration())
    job = KpiTimeseriesDeliveryJob(
        configuration=configuration,
        historian_state=_Historian(historian),
        history=history,
        checkpoint=checkpoint,
        snapshots=publisher,
        now=lambda: datetime(2026, 8, 25, 11, 1, 31, tzinfo=UTC),
    )

    result = job.run_iteration(_context(tmp_path))

    assert result.status is KpiTimeseriesDeliveryIterationStatus.PUBLISHED
    assert result.watermark_utc == '2026-08-25T11:00:00Z'
    assert history.calls[0]['start_utc'] == datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
    assert history.calls[0]['end_utc'] == datetime(2026, 8, 25, 11, 0, tzinfo=UTC)
    assert history.calls[0]['step_seconds'] == 120
    assert len(publisher.snapshots[0].windows[0].values[0]) == 30
    assert publisher.snapshots[0].windows[0].values[0][-1] == 20.0
    assert configuration.calls == 1


def test_same_effective_end_and_configuration_skips_without_parquet_read(tmp_path) -> None:
    effective = KpiWatermark(datetime(2026, 8, 25, 11, 0, tzinfo=UTC))
    historian = KpiWatermark(datetime(2026, 8, 25, 11, 1, 30, tzinfo=UTC))
    history = _History()
    publisher = _Publisher()
    job = KpiTimeseriesDeliveryJob(
        configuration=_Configuration(_configuration()),
        historian_state=_Historian(historian),
        history=history,
        checkpoint=_Checkpoint(KpiTimeseriesCheckpoint(effective, 'config-1')),
        snapshots=publisher,
    )

    result = job.run_iteration(_context(tmp_path))

    assert result.status is KpiTimeseriesDeliveryIterationStatus.SKIPPED_CURRENT
    assert history.calls == []
    assert publisher.snapshots == []


def test_configuration_change_reprojects_same_effective_end(tmp_path) -> None:
    effective = KpiWatermark(datetime(2026, 8, 25, 11, 0, tzinfo=UTC))
    historian = KpiWatermark(datetime(2026, 8, 25, 11, 1, 30, tzinfo=UTC))
    history = _History()
    job = KpiTimeseriesDeliveryJob(
        configuration=_Configuration(_configuration('config-2')),
        historian_state=_Historian(historian),
        history=history,
        checkpoint=_Checkpoint(KpiTimeseriesCheckpoint(effective, 'config-1')),
        snapshots=_Publisher(),
    )

    result = job.run_iteration(_context(tmp_path))

    assert result.status is KpiTimeseriesDeliveryIterationStatus.PUBLISHED
    assert len(history.calls) == 1


def test_publish_failure_does_not_advance_checkpoint(tmp_path) -> None:
    old = KpiWatermark(datetime(2026, 8, 25, 10, 58, tzinfo=UTC))
    historian = KpiWatermark(datetime(2026, 8, 25, 11, 0, tzinfo=UTC))
    checkpoint = _Checkpoint(KpiTimeseriesCheckpoint(old, 'config-1'))

    class FailingPublisher:
        def publish(self, snapshot):
            raise RuntimeError('publish failed')

    job = KpiTimeseriesDeliveryJob(
        configuration=_Configuration(_configuration()),
        historian_state=_Historian(historian),
        history=_History(),
        checkpoint=checkpoint,
        snapshots=FailingPublisher(),
    )

    with pytest.raises(RuntimeError, match='publish failed'):
        job.run_iteration(_context(tmp_path))

    assert checkpoint.value == KpiTimeseriesCheckpoint(old, 'config-1')


def test_missing_historian_watermark_after_progress_fails(tmp_path) -> None:
    checkpoint = KpiTimeseriesCheckpoint(
        KpiWatermark(datetime(2026, 8, 25, 11, 0, tzinfo=UTC)),
        'config-1',
    )
    job = KpiTimeseriesDeliveryJob(
        configuration=_Configuration(_configuration()),
        historian_state=_Historian(None),
        history=_History(),
        checkpoint=_Checkpoint(checkpoint),
        snapshots=_Publisher(),
    )

    with pytest.raises(
        KpiTimeseriesDeliveryRepositoryError, match='missing after delivery progress'
    ):
        job.run_iteration(_context(tmp_path))


def test_same_missing_historian_watermark_does_not_recommit_checkpoint(tmp_path) -> None:
    checkpoint = _Checkpoint(KpiTimeseriesCheckpoint(None, 'config-1'))
    job = KpiTimeseriesDeliveryJob(
        configuration=_Configuration(_configuration()),
        historian_state=_Historian(None),
        history=_History(),
        checkpoint=checkpoint,
        snapshots=_Publisher(),
    )

    result = job.run_iteration(_context(tmp_path))

    assert result.status is KpiTimeseriesDeliveryIterationStatus.SKIPPED_NO_WATERMARK
    assert checkpoint.commits == []


def test_missing_historian_watermark_does_not_advance_configuration_checkpoint(tmp_path) -> None:
    checkpoint = _Checkpoint(KpiTimeseriesCheckpoint(None, 'config-0'))
    job = KpiTimeseriesDeliveryJob(
        configuration=_Configuration(_configuration('config-1')),
        historian_state=_Historian(None),
        history=_History(),
        checkpoint=checkpoint,
        snapshots=_Publisher(),
    )

    result = job.run_iteration(_context(tmp_path))

    assert result.status is KpiTimeseriesDeliveryIterationStatus.SKIPPED_NO_WATERMARK
    assert checkpoint.value == KpiTimeseriesCheckpoint(None, 'config-0')
    assert checkpoint.commits == []


def test_regressed_historian_watermark_fails_before_history_read_or_publish(tmp_path) -> None:
    dispatched = KpiWatermark(datetime(2026, 8, 25, 11, 0, tzinfo=UTC))
    historian = KpiWatermark(datetime(2026, 8, 25, 10, 58, 30, tzinfo=UTC))
    history = _History()
    publisher = _Publisher()
    job = KpiTimeseriesDeliveryJob(
        configuration=_Configuration(_configuration()),
        historian_state=_Historian(historian),
        history=history,
        checkpoint=_Checkpoint(KpiTimeseriesCheckpoint(dispatched, 'config-1')),
        snapshots=publisher,
    )

    with pytest.raises(KpiTimeseriesDeliveryRepositoryError, match='must not regress'):
        job.run_iteration(_context(tmp_path))

    assert history.calls == []
    assert publisher.snapshots == []
