from datetime import UTC, datetime

import pytest

from ada.kpis.core import KpiArea, KpiEvaluation, KpiResult, KpiStatus, KpiValueKind, KpiWatermark
from ada.kpis.delivery import KpiDeliveryConfiguration
from ada.processes.kpis_delivery import (
    KpiDeliveryCheckpoint,
    KpiLatestDeliveryIterationStatus,
    KpiLatestDeliveryJob,
    KpiLatestPublication,
    KpiLatestPublicationStatus,
)
from ada.processes.kpis_delivery.errors import KpiDeliveryRepositoryError
from atlanticus.kernel import Environment
from atlanticus.runtime import JobDefinition, JobRuntimeContext, RuntimeConfiguration

_NOW = datetime(2026, 8, 25, 10, 0, 31, tzinfo=UTC)


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
                        'series_enabled': False,
                        'series_hours': None,
                    }
                ]
            },
        }
    )


def _evaluation(watermark: KpiWatermark) -> KpiEvaluation:
    return KpiEvaluation(
        watermark=watermark,
        results=(
            KpiResult(
                key='production',
                area=KpiArea.PLANTA,
                status=KpiStatus.OK,
                value_kind=KpiValueKind.VALUE,
                persist_history=True,
                value=66.0,
                parsed_value='66,00',
            ),
        ),
    )


class _Configuration:
    def __init__(self, value: KpiDeliveryConfiguration) -> None:
        self.value = value
        self.calls = 0

    def read(self) -> KpiDeliveryConfiguration:
        self.calls += 1
        return self.value


class _State:
    def __init__(self, watermark: KpiWatermark | None) -> None:
        self.watermark = watermark

    def read_watermark(self) -> KpiWatermark | None:
        return self.watermark


class _Latest:
    def __init__(self, value: KpiEvaluation | None) -> None:
        self.value = value
        self.calls = 0

    def read(self) -> KpiEvaluation | None:
        self.calls += 1
        return self.value


class _Checkpoint:
    def __init__(self, value: KpiDeliveryCheckpoint | None = None) -> None:
        self.value = value
        self.commits: list[KpiDeliveryCheckpoint] = []

    def read(self) -> KpiDeliveryCheckpoint | None:
        return self.value

    def commit(self, value: KpiDeliveryCheckpoint) -> KpiDeliveryCheckpoint:
        self.value = value
        self.commits.append(value)
        return value


class _Publisher:
    def __init__(
        self,
        status: KpiLatestPublicationStatus = KpiLatestPublicationStatus.PUBLISHED,
    ) -> None:
        self.status = status
        self.snapshots = []

    def publish(self, snapshot):
        self.snapshots.append(snapshot)
        return KpiLatestPublication(status=self.status, revision=snapshot.manifest.revision)


def _context(tmp_path) -> JobRuntimeContext:
    definition = JobDefinition(
        module_name='ada.processes.kpis_delivery',
        service_name='kpis-delivery',
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
        correlation_id='correlation-1',
    )


def test_job_freezes_configuration_and_skips_current_checkpoint(tmp_path) -> None:
    watermark = KpiWatermark(datetime(2026, 8, 25, 10, 0, 30, tzinfo=UTC))
    configuration = _Configuration(_configuration())
    latest = _Latest(_evaluation(watermark))
    checkpoint = _Checkpoint(KpiDeliveryCheckpoint(watermark, 'config-1'))
    publisher = _Publisher()
    job = KpiLatestDeliveryJob(
        configuration=configuration,
        kpi_state=_State(watermark),
        latest=latest,
        checkpoint=checkpoint,
        snapshots=publisher,
        now=lambda: _NOW,
    )

    first = job.run_iteration(_context(tmp_path))
    second = job.run_iteration(_context(tmp_path))

    assert first.status is KpiLatestDeliveryIterationStatus.SKIPPED_CURRENT
    assert second.status is KpiLatestDeliveryIterationStatus.SKIPPED_CURRENT
    assert configuration.calls == 1
    assert latest.calls == 0
    assert publisher.snapshots == []


def test_new_watermark_publishes_even_when_value_can_repeat(tmp_path) -> None:
    old = KpiWatermark(datetime(2026, 8, 25, 10, 0, tzinfo=UTC))
    new = KpiWatermark(datetime(2026, 8, 25, 10, 0, 30, tzinfo=UTC))
    checkpoint = _Checkpoint(KpiDeliveryCheckpoint(old, 'config-1'))
    publisher = _Publisher()
    job = KpiLatestDeliveryJob(
        configuration=_Configuration(_configuration()),
        kpi_state=_State(new),
        latest=_Latest(_evaluation(new)),
        checkpoint=checkpoint,
        snapshots=publisher,
        now=lambda: _NOW,
    )

    result = job.run_iteration(_context(tmp_path))

    assert result.status is KpiLatestDeliveryIterationStatus.PUBLISHED
    assert result.watermark_utc == new.text
    assert publisher.snapshots[0].destinations['global']['production'].value == '66,00'
    assert checkpoint.value == KpiDeliveryCheckpoint(new, 'config-1')


def test_configuration_change_publishes_without_new_kpi_watermark(tmp_path) -> None:
    watermark = KpiWatermark(datetime(2026, 8, 25, 10, 0, 30, tzinfo=UTC))
    checkpoint = _Checkpoint(KpiDeliveryCheckpoint(watermark, 'config-0'))
    job = KpiLatestDeliveryJob(
        configuration=_Configuration(_configuration('config-1')),
        kpi_state=_State(watermark),
        latest=_Latest(_evaluation(watermark)),
        checkpoint=checkpoint,
        snapshots=_Publisher(),
        now=lambda: _NOW,
    )

    result = job.run_iteration(_context(tmp_path))

    assert result.status is KpiLatestDeliveryIterationStatus.PUBLISHED
    assert checkpoint.value.configuration_revision == 'config-1'


def test_checkpoint_is_not_advanced_when_publish_fails(tmp_path) -> None:
    old = KpiWatermark(datetime(2026, 8, 25, 10, 0, tzinfo=UTC))
    new = KpiWatermark(datetime(2026, 8, 25, 10, 0, 30, tzinfo=UTC))
    checkpoint = _Checkpoint(KpiDeliveryCheckpoint(old, 'config-1'))

    class FailingPublisher:
        def publish(self, snapshot):
            raise RuntimeError('publish failed')

    job = KpiLatestDeliveryJob(
        configuration=_Configuration(_configuration()),
        kpi_state=_State(new),
        latest=_Latest(_evaluation(new)),
        checkpoint=checkpoint,
        snapshots=FailingPublisher(),
        now=lambda: _NOW,
    )

    with pytest.raises(RuntimeError, match='publish failed'):
        job.run_iteration(_context(tmp_path))

    assert checkpoint.value == KpiDeliveryCheckpoint(old, 'config-1')


def test_latest_must_match_committed_watermark(tmp_path) -> None:
    committed = KpiWatermark(datetime(2026, 8, 25, 10, 0, 30, tzinfo=UTC))
    stale = KpiWatermark(datetime(2026, 8, 25, 10, 0, tzinfo=UTC))
    job = KpiLatestDeliveryJob(
        configuration=_Configuration(_configuration()),
        kpi_state=_State(committed),
        latest=_Latest(_evaluation(stale)),
        checkpoint=_Checkpoint(),
        snapshots=_Publisher(),
        now=lambda: _NOW,
    )

    with pytest.raises(KpiDeliveryRepositoryError, match='does not match'):
        job.run_iteration(_context(tmp_path))


def test_missing_committed_watermark_after_progress_fails(tmp_path) -> None:
    watermark = KpiWatermark(datetime(2026, 8, 25, 10, 0, 30, tzinfo=UTC))
    job = KpiLatestDeliveryJob(
        configuration=_Configuration(_configuration()),
        kpi_state=_State(None),
        latest=_Latest(None),
        checkpoint=_Checkpoint(KpiDeliveryCheckpoint(watermark, 'config-1')),
        snapshots=_Publisher(),
        now=lambda: _NOW,
    )

    with pytest.raises(KpiDeliveryRepositoryError, match='missing after delivery progress'):
        job.run_iteration(_context(tmp_path))


def test_regressed_committed_watermark_fails_before_latest_read_or_publish(tmp_path) -> None:
    dispatched = KpiWatermark(datetime(2026, 8, 25, 10, 1, tzinfo=UTC))
    regressed = KpiWatermark(datetime(2026, 8, 25, 10, 0, 30, tzinfo=UTC))
    latest = _Latest(_evaluation(regressed))
    publisher = _Publisher()
    job = KpiLatestDeliveryJob(
        configuration=_Configuration(_configuration()),
        kpi_state=_State(regressed),
        latest=latest,
        checkpoint=_Checkpoint(KpiDeliveryCheckpoint(dispatched, 'config-1')),
        snapshots=publisher,
        now=lambda: _NOW,
    )

    with pytest.raises(KpiDeliveryRepositoryError, match='must not regress'):
        job.run_iteration(_context(tmp_path))

    assert latest.calls == 0
    assert publisher.snapshots == []
