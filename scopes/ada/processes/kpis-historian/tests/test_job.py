import pytest

from ada.kpis.persistence import KpiCommitStore, KpiEvaluationRepository, KpiPersistencePaths
from ada.processes.kpis_historian.errors import KpiHistorianWatermarkError
from ada.processes.kpis_historian.history import KpiHistoryWriter, history_definition
from ada.processes.kpis_historian.job import KpiHistorianIterationStatus, KpiHistorianJob
from ada.processes.kpis_historian.state import KpiHistorianCommitStore
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.json import JsonDocumentStore
from atlanticus.state import AtomicStateStore
from tests.support import context, evaluation, watermark


def _job(tmp_path):
    application = 'ada-operaciones-integradas-local'
    state_store = AtomicStateStore(volume_path=tmp_path, application=application)
    kpi_state = KpiCommitStore(store=state_store)
    historian_state = KpiHistorianCommitStore(store=state_store)
    evaluations = KpiEvaluationRepository(
        store=JsonDocumentStore(),
        paths=KpiPersistencePaths(tmp_path / application),
    )
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / application / 'datasets'))
    job = KpiHistorianJob(
        evaluations=evaluations,
        kpi_state=kpi_state,
        historian_state=historian_state,
        history=KpiHistoryWriter(runtime=runtime),
    )
    return job, evaluations, kpi_state, historian_state, runtime


def test_no_kpi_committed_watermark_skips_without_historian_state(tmp_path) -> None:
    job, _, _, historian_state, _ = _job(tmp_path)
    runtime_context = context(tmp_path)

    result = job.run_iteration(runtime_context)

    assert result.status is KpiHistorianIterationStatus.SKIPPED_NO_KPI_WATERMARK
    assert historian_state.read_watermark() is None
    assert not runtime_context.iteration_has_work


def test_historian_catches_up_only_real_evaluations_and_commits_latest_actual_tick(
    tmp_path,
) -> None:
    job, evaluations, kpi_state, historian_state, runtime = _job(tmp_path)
    t1 = watermark(19, 10)
    t2 = watermark(19, 20)
    t5 = watermark(19, 50)
    evaluations.write_once(evaluation(t1, key='kpi-a', value=1))
    evaluations.write_once(evaluation(t2, key='kpi-a', value=2))
    evaluations.write_once(evaluation(t5, key='kpi-a', value=5))
    kpi_state.commit_watermark(t5)
    runtime_context = context(tmp_path)

    result = job.run_iteration(runtime_context)

    assert result.status is KpiHistorianIterationStatus.PROCESSED
    assert result.evaluations_processed == 3
    assert result.history_rows == 3
    assert historian_state.read_watermark() == t5
    target = history_definition().resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '19'},
    )
    table = runtime.read_table(definition=history_definition(), target=target).table
    assert table.column('timestamp_utc').to_pylist() == [
        t1.timestamp_utc,
        t2.timestamp_utc,
        t5.timestamp_utc,
    ]
    assert runtime_context.iteration_has_work
    assert runtime_context.get_iteration_fact('evaluations_processed') == 3


def test_evaluation_without_historical_results_still_advances_historian_watermark(tmp_path) -> None:
    job, evaluations, kpi_state, historian_state, _ = _job(tmp_path)
    target = watermark(19, 10)
    evaluations.write_once(evaluation(target, persist_history=False))
    kpi_state.commit_watermark(target)

    result = job.run_iteration(context(tmp_path))

    assert result.status is KpiHistorianIterationStatus.PROCESSED
    assert result.evaluations_processed == 1
    assert result.history_rows == 0
    assert result.history_publications == 0
    assert historian_state.read_watermark() == target


def test_equal_kpi_and_historian_watermarks_skip(tmp_path) -> None:
    job, evaluations, kpi_state, historian_state, _ = _job(tmp_path)
    target = watermark(19, 10)
    evaluations.write_once(evaluation(target))
    kpi_state.commit_watermark(target)
    historian_state.commit_watermark(target)

    result = job.run_iteration(context(tmp_path))

    assert result.status is KpiHistorianIterationStatus.SKIPPED_CURRENT


def test_historian_watermark_ahead_of_kpi_is_error(tmp_path) -> None:
    job, _, kpi_state, historian_state, _ = _job(tmp_path)
    kpi_state.commit_watermark(watermark(19, 10))
    historian_state.commit_watermark(watermark(19, 20))

    with pytest.raises(KpiHistorianWatermarkError, match='must not be greater'):
        job.run_iteration(context(tmp_path))


def test_missing_committed_evaluation_is_error_and_does_not_advance_state(tmp_path) -> None:
    job, evaluations, kpi_state, historian_state, _ = _job(tmp_path)
    t1 = watermark(19, 10)
    committed = watermark(19, 50)
    evaluations.write_once(evaluation(t1))
    kpi_state.commit_watermark(committed)

    with pytest.raises(KpiHistorianWatermarkError, match='does not match'):
        job.run_iteration(context(tmp_path))

    assert historian_state.read_watermark() is None
