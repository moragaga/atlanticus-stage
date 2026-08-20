from datetime import UTC, datetime

from ada.kpis.core import (
    KpiArea,
    KpiEvaluation,
    KpiResult,
    KpiStatus,
    KpiValueKind,
    KpiWatermark,
)
from ada.kpis.delivery import KpiDeliveryBinding
from ada.processes.kpis_delivery import (
    KpiLatestDeliveryJob,
    KpiLatestPublication,
    KpiLatestPublicationStatus,
)
from atlanticus.kernel import Environment
from atlanticus.runtime import JobDefinition, JobRuntimeContext, RuntimeConfiguration

_NOW = datetime(2026, 8, 20, 15, 15, tzinfo=UTC)


class FakeLatestReader:
    def __init__(self, evaluation: KpiEvaluation | None) -> None:
        self.evaluation = evaluation
        self.calls = 0

    def read(self) -> KpiEvaluation | None:
        self.calls += 1
        return self.evaluation


class FakeBindingsReader:
    def __init__(self, bindings: tuple[KpiDeliveryBinding, ...]) -> None:
        self.bindings = bindings
        self.calls = 0

    def read_bindings(self) -> tuple[KpiDeliveryBinding, ...]:
        self.calls += 1
        return self.bindings


class FakeSnapshotRepository:
    def __init__(self, status: KpiLatestPublicationStatus) -> None:
        self.status = status
        self.snapshots = []

    def publish(self, snapshot):
        self.snapshots.append(snapshot)
        return KpiLatestPublication(status=self.status, revision=snapshot.manifest.revision)


def _context(tmp_path) -> JobRuntimeContext:
    definition = JobDefinition(
        module_name='ada.processes.kpis_delivery',
        service_name='kpis-delivery',
        job_key='kpis-delivery-test',
        iteration_timeout_seconds=30,
        execution_timeout_seconds=60,
        shutdown_grace_seconds=5,
        lease_timeout_seconds=30,
        lease_renew_seconds=10,
    )
    configuration = RuntimeConfiguration(
        environment=Environment.from_value('local'),
        application='ada-kpis-delivery-test',
        volume_path=tmp_path,
    )
    return JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='run-1',
        correlation_id='correlation-1',
    )


def _evaluation() -> KpiEvaluation:
    return KpiEvaluation(
        watermark=KpiWatermark(datetime(2026, 8, 20, 12, 55, 30, tzinfo=UTC)),
        results=(
            KpiResult(
                key='tonelaje',
                area=KpiArea.PLANTA,
                status=KpiStatus.OK,
                value_kind=KpiValueKind.VALUE,
                persist_history=True,
                value=66.0,
                parsed_value='66,00',
            ),
            KpiResult(
                key='utilizacion',
                area=KpiArea.PLANTA,
                status=KpiStatus.ERROR,
                value_kind=KpiValueKind.VALUE,
                persist_history=True,
                error='RuntimeError',
            ),
        ),
    )


def _job(*, latest, bindings, snapshots) -> KpiLatestDeliveryJob:
    return KpiLatestDeliveryJob(
        latest=latest,
        bindings=bindings,
        snapshots=snapshots,
        now=lambda: _NOW,
    )


def test_job_projects_latest_and_publishes_snapshot(tmp_path) -> None:
    latest = FakeLatestReader(_evaluation())
    bindings = FakeBindingsReader(
        (
            KpiDeliveryBinding(store_key='chancado', kpi_key='tonelaje'),
            KpiDeliveryBinding(store_key='chancado', kpi_key='utilizacion'),
            KpiDeliveryBinding(store_key='chancado', kpi_key='inexistente'),
        )
    )
    snapshots = FakeSnapshotRepository(KpiLatestPublicationStatus.PUBLISHED)
    context = _context(tmp_path)

    result = _job(latest=latest, bindings=bindings, snapshots=snapshots).run_iteration(context)

    assert result.publication.status is KpiLatestPublicationStatus.PUBLISHED
    assert result.store_count == 1
    assert result.value_count == 3
    assert result.missing_count == 1
    assert result.error_count == 1
    assert latest.calls == 1
    assert bindings.calls == 1
    assert snapshots.snapshots[0].as_document()['stores']['chancado'] == {
        'tonelaje': {'status': 'ok', 'value_kind': 'value', 'value': '66,00'},
        'utilizacion': {'status': 'error', 'value_kind': 'value', 'value': None},
        'inexistente': {'status': 'missing', 'value_kind': None, 'value': None},
    }
    assert context.get_iteration_fact('outcome') == 'completed'
    assert context.get_iteration_fact('reason') == 'published'
    assert context.iteration_has_work is True
    assert context.get_execution_fact('snapshots_published') == 1


def test_job_skips_upsert_work_when_revision_is_unchanged(tmp_path) -> None:
    latest = FakeLatestReader(_evaluation())
    bindings = FakeBindingsReader((KpiDeliveryBinding(store_key='chancado', kpi_key='tonelaje'),))
    snapshots = FakeSnapshotRepository(KpiLatestPublicationStatus.UNCHANGED)
    context = _context(tmp_path)

    result = _job(latest=latest, bindings=bindings, snapshots=snapshots).run_iteration(context)

    assert result.publication.status is KpiLatestPublicationStatus.UNCHANGED
    assert context.get_iteration_fact('outcome') == 'skipped'
    assert context.get_iteration_fact('reason') == 'unchanged'
    assert context.iteration_has_work is False
    assert context.get_execution_fact('snapshots_published') is None


def test_job_projects_missing_when_latest_does_not_exist(tmp_path) -> None:
    latest = FakeLatestReader(None)
    bindings = FakeBindingsReader(
        (
            KpiDeliveryBinding(store_key='chancado', kpi_key='tonelaje'),
            KpiDeliveryBinding(store_key='time-view', kpi_key='hora_pi'),
        )
    )
    snapshots = FakeSnapshotRepository(KpiLatestPublicationStatus.PUBLISHED)
    context = _context(tmp_path)

    result = _job(latest=latest, bindings=bindings, snapshots=snapshots).run_iteration(context)

    assert result.value_count == 2
    assert result.missing_count == 2
    assert snapshots.snapshots[0].as_document()['stores'] == {
        'chancado': {
            'tonelaje': {'status': 'missing', 'value_kind': None, 'value': None},
        },
        'time-view': {
            'hora_pi': {'status': 'missing', 'value_kind': None, 'value': None},
        },
    }


def test_empty_configuration_publishes_minimum_snapshot_without_reading_latest(tmp_path) -> None:
    latest = FakeLatestReader(_evaluation())
    bindings = FakeBindingsReader(())
    snapshots = FakeSnapshotRepository(KpiLatestPublicationStatus.PUBLISHED)
    context = _context(tmp_path)

    result = _job(latest=latest, bindings=bindings, snapshots=snapshots).run_iteration(context)

    assert latest.calls == 0
    assert result.store_count == 0
    assert result.value_count == 0
    assert result.missing_count == 0
    assert snapshots.snapshots[0].as_document()['stores'] == {}
