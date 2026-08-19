from datetime import UTC, datetime

import pandas as pd
import pytest

from ada.kpis.core import KpiArea, KpiCatalog, KpiMode, KpiSource, KpiSpec, KpiWatermark
from ada.kpis.evaluation import KpiEvaluator
from ada.kpis.persistence import (
    KpiCommitStore,
    KpiEvaluationCommitter,
    KpiEvaluationRepository,
    KpiEvaluationWriteStatus,
    KpiLatestRepository,
    KpiPersistencePaths,
)
from ada.kpis.planner import KpiRequirementPlanner
from ada.kpis.sources import KpiSourceBinding, KpiSourceRegistry, LoadedKpiSource, LoadedKpiSources
from ada.processes.kpis.clock import PiClockSnapshot
from ada.processes.kpis.errors import KpiProcessWatermarkError
from ada.processes.kpis.job import KpiIterationStatus, KpiProcessJob
from atlanticus.datasets.layouts import SingleArtifactLayout
from atlanticus.datasets.models import DatasetDefinition, DatasetKey, MaterializationDefinition
from atlanticus.json import JsonDocumentStore
from atlanticus.kernel import Environment
from atlanticus.runtime import JobDefinition, JobRuntimeContext, RuntimeConfiguration
from atlanticus.state import AtomicStateStore


class Clock:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def current(self):
        return self.snapshot


class SourceLoader:
    def __init__(self, catalog, value=10):
        self.catalog = catalog
        self.value = value
        self.calls = 0
        self.binding = _binding()

    def load(self, *, plan, watermark):
        self.calls += 1
        loaded = LoadedKpiSource(
            source=KpiSource.PI_INTERPOLATED,
            snapshot_frame=pd.DataFrame({'value': [self.value]}),
        )
        return LoadedKpiSources(
            watermark=watermark,
            plan=plan,
            registry=KpiSourceRegistry({KpiSource.PI_INTERPOLATED: self.binding}),
            loaded={KpiSource.PI_INTERPOLATED: loaded},
            failures={},
        )


def _binding():
    definition = DatasetDefinition(
        key=DatasetKey(namespace=('pi', 'web-api'), name='interpolated'),
        materializations=(MaterializationDefinition(name='latest', layout=SingleArtifactLayout()),),
    )
    return KpiSourceBinding(
        source=KpiSource.PI_INTERPOLATED,
        definition=definition,
        snapshot_materialization='latest',
        timestamp_column='timestamp_utc',
    )


def _catalog():
    return KpiCatalog(
        specs=(
            KpiSpec(
                key='value',
                area=KpiArea.GENERAL,
                mode=KpiMode.LATEST_NUMBER,
                source=KpiSource.PI_INTERPOLATED,
                columns=('value',),
            ),
        )
    )


def _watermark(second):
    return KpiWatermark(datetime(2026, 8, 19, 20, 0, second, tzinfo=UTC))


def _context(tmp_path) -> JobRuntimeContext:
    definition = JobDefinition(
        module_name='ada.processes.kpis',
        service_name='kpis',
        job_key='kpi-evaluation-test',
        iteration_timeout_seconds=30,
        execution_timeout_seconds=60,
        shutdown_grace_seconds=5,
        lease_timeout_seconds=30,
        lease_renew_seconds=10,
    )
    configuration = RuntimeConfiguration(
        environment=Environment.from_value('local'),
        application='ada',
        volume_path=tmp_path,
    )
    return JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='run-id',
        correlation_id='correlation-id',
    )


def _job(tmp_path, *, snapshot, source_loader=None):
    catalog = _catalog()
    loader = source_loader or SourceLoader(catalog)
    evaluator = KpiEvaluator(source_loader=loader, planner=KpiRequirementPlanner())
    state_store = AtomicStateStore(volume_path=tmp_path, application='ada')
    state = KpiCommitStore(store=state_store)
    paths = KpiPersistencePaths(tmp_path / 'ada')
    json_store = JsonDocumentStore()
    evaluations = KpiEvaluationRepository(store=json_store, paths=paths)
    latest = KpiLatestRepository(store=json_store, paths=paths)
    committer = KpiEvaluationCommitter(evaluations=evaluations, latest=latest, state=state)
    job = KpiProcessJob(
        catalog=catalog,
        clock=Clock(snapshot),
        evaluator=evaluator,
        evaluations=evaluations,
        committer=committer,
        state=state,
    )
    return job, loader, state, evaluations, latest


def test_no_pi_watermark_skips_without_creating_kpi_state(tmp_path) -> None:
    snapshot = PiClockSnapshot(
        watermark=None,
        source_watermarks={KpiSource.PI_INTERPOLATED: None},
    )
    job, loader, state, _, _ = _job(tmp_path, snapshot=snapshot)
    context = _context(tmp_path)

    result = job.run_iteration(context)

    assert result.status is KpiIterationStatus.SKIPPED_NO_PI_WATERMARK
    assert state.read_watermark() is None
    assert loader.calls == 0
    assert not context.iteration_has_work
    assert context.get_iteration_fact('outcome') == 'skipped'


def test_first_pi_watermark_is_evaluated_and_committed(tmp_path) -> None:
    target = _watermark(10)
    snapshot = PiClockSnapshot(
        watermark=target,
        source_watermarks={KpiSource.PI_INTERPOLATED: target},
    )
    job, loader, state, evaluations, latest = _job(tmp_path, snapshot=snapshot)
    context = _context(tmp_path)

    result = job.run_iteration(context)

    assert result.status is KpiIterationStatus.EVALUATED
    assert result.write_status is KpiEvaluationWriteStatus.CREATED
    assert state.read_watermark() == target
    assert evaluations.read(target) is not None
    assert latest.read().watermark == target
    assert loader.calls == 1
    assert context.iteration_has_work
    assert context.get_iteration_fact('outcome') == 'completed'


def test_equal_pi_and_kpi_watermarks_skip(tmp_path) -> None:
    target = _watermark(10)
    snapshot = PiClockSnapshot(
        watermark=target,
        source_watermarks={KpiSource.PI_INTERPOLATED: target},
    )
    job, loader, state, _, _ = _job(tmp_path, snapshot=snapshot)
    state.commit_watermark(target)

    result = job.run_iteration(_context(tmp_path))

    assert result.status is KpiIterationStatus.SKIPPED_CURRENT
    assert loader.calls == 0


def test_pi_watermark_greater_than_committed_evaluates_only_current_target(tmp_path) -> None:
    committed = _watermark(10)
    target = _watermark(40)
    snapshot = PiClockSnapshot(
        watermark=target,
        source_watermarks={KpiSource.PI_INTERPOLATED: target},
    )
    job, loader, state, evaluations, _ = _job(tmp_path, snapshot=snapshot)
    state.commit_watermark(committed)

    result = job.run_iteration(_context(tmp_path))

    assert result.status is KpiIterationStatus.EVALUATED
    assert state.read_watermark() == target
    assert evaluations.read(target) is not None
    assert evaluations.read(_watermark(20)) is None
    assert evaluations.read(_watermark(30)) is None
    assert loader.calls == 1


def test_pi_watermark_lower_than_committed_is_error(tmp_path) -> None:
    committed = _watermark(40)
    target = _watermark(10)
    snapshot = PiClockSnapshot(
        watermark=target,
        source_watermarks={KpiSource.PI_INTERPOLATED: target},
    )
    job, loader, state, _, _ = _job(tmp_path, snapshot=snapshot)
    state.commit_watermark(committed)
    context = _context(tmp_path)

    with pytest.raises(KpiProcessWatermarkError, match='must not be lower'):
        job.run_iteration(context)
    assert loader.calls == 0
    assert context.get_iteration_fact('reason') == 'pi_watermark_regression'


def test_same_target_retry_reuses_persisted_evaluation_instead_of_recalculating(tmp_path) -> None:
    target = _watermark(10)
    snapshot = PiClockSnapshot(
        watermark=target,
        source_watermarks={KpiSource.PI_INTERPOLATED: target},
    )
    job, loader, state, evaluations, _ = _job(tmp_path, snapshot=snapshot)
    evaluation = job._evaluator.evaluate(
        catalog=job._catalog,
        watermark=target,
        source_watermarks=snapshot.source_watermarks,
    )
    evaluations.write_once(evaluation)
    loader.calls = 0

    result = job.run_iteration(_context(tmp_path))

    assert result.status is KpiIterationStatus.RETRIED_PERSISTED_EVALUATION
    assert result.write_status is KpiEvaluationWriteStatus.UNCHANGED
    assert state.read_watermark() == target
    assert loader.calls == 0


def test_retry_finishes_real_persisted_evaluation_before_newer_pi_without_recalculation(
    tmp_path,
) -> None:
    committed = _watermark(10)
    pending = _watermark(20)
    current = _watermark(40)
    snapshot = PiClockSnapshot(
        watermark=current,
        source_watermarks={KpiSource.PI_INTERPOLATED: current},
    )
    job, loader, state, evaluations, _ = _job(tmp_path, snapshot=snapshot)
    state.commit_watermark(committed)
    persisted = job._evaluator.evaluate(
        catalog=job._catalog,
        watermark=pending,
        source_watermarks={KpiSource.PI_INTERPOLATED: pending},
    )
    evaluations.write_once(persisted)
    loader.calls = 0

    recovered = job.run_iteration(_context(tmp_path))

    assert recovered.status is KpiIterationStatus.RETRIED_PERSISTED_EVALUATION
    assert recovered.pi_watermark == pending
    assert state.read_watermark() == pending
    assert loader.calls == 0

    evaluated = job.run_iteration(_context(tmp_path))

    assert evaluated.status is KpiIterationStatus.EVALUATED
    assert evaluated.pi_watermark == current
    assert state.read_watermark() == current
    assert evaluations.read(_watermark(30)) is None
    assert loader.calls == 1
