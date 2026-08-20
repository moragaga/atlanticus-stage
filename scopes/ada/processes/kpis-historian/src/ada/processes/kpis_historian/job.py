from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ada.kpis.core import KpiWatermark
from ada.kpis.persistence import KpiCommitStore, KpiEvaluationRepository
from ada.processes.kpis_historian.errors import KpiHistorianWatermarkError
from ada.processes.kpis_historian.history import KpiHistoryWriter
from ada.processes.kpis_historian.state import KpiHistorianCommitStore
from atlanticus.runtime import JobRuntimeContext


class KpiHistorianIterationStatus(StrEnum):
    SKIPPED_NO_KPI_WATERMARK = 'skipped_no_kpi_watermark'
    SKIPPED_CURRENT = 'skipped_current'
    PROCESSED = 'processed'


@dataclass(frozen=True, slots=True)
class KpiHistorianIterationResult:
    status: KpiHistorianIterationStatus
    kpi_committed: KpiWatermark | None
    historian_before: KpiWatermark | None
    historian_after: KpiWatermark | None
    evaluations_processed: int = 0
    history_rows: int = 0
    error_rows: int = 0
    history_publications: int = 0
    error_publications: int = 0


class KpiHistorianJob:
    def __init__(
        self,
        *,
        evaluations: KpiEvaluationRepository,
        kpi_state: KpiCommitStore,
        historian_state: KpiHistorianCommitStore,
        history: KpiHistoryWriter,
    ) -> None:
        if not isinstance(evaluations, KpiEvaluationRepository):
            raise TypeError('evaluations must be KpiEvaluationRepository')
        if not isinstance(kpi_state, KpiCommitStore):
            raise TypeError('kpi_state must be KpiCommitStore')
        if not isinstance(historian_state, KpiHistorianCommitStore):
            raise TypeError('historian_state must be KpiHistorianCommitStore')
        if not isinstance(history, KpiHistoryWriter):
            raise TypeError('history must be KpiHistoryWriter')
        self._evaluations = evaluations
        self._kpi_state = kpi_state
        self._historian_state = historian_state
        self._history = history

    def run_iteration(self, context: JobRuntimeContext) -> KpiHistorianIterationResult:
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be a JobRuntimeContext')
        context.raise_if_cancelled()
        kpi_committed = self._kpi_state.read_watermark()
        historian_before = self._historian_state.read_watermark()

        if kpi_committed is None:
            if historian_before is not None:
                _watermark_failure(
                    context,
                    reason='kpi_committed_watermark_missing',
                    kpi_committed=None,
                    historian_committed=historian_before,
                )
                raise KpiHistorianWatermarkError(
                    'KPI committed watermark is missing while historian state already exists'
                )
            return _record_result(
                context,
                KpiHistorianIterationResult(
                    status=KpiHistorianIterationStatus.SKIPPED_NO_KPI_WATERMARK,
                    kpi_committed=None,
                    historian_before=None,
                    historian_after=None,
                ),
            )

        if historian_before is not None and historian_before > kpi_committed:
            _watermark_failure(
                context,
                reason='historian_watermark_ahead_of_kpi',
                kpi_committed=kpi_committed,
                historian_committed=historian_before,
            )
            raise KpiHistorianWatermarkError(
                'KPI historian committed watermark must not be greater than KPI committed watermark'
            )

        if historian_before == kpi_committed:
            return _record_result(
                context,
                KpiHistorianIterationResult(
                    status=KpiHistorianIterationStatus.SKIPPED_CURRENT,
                    kpi_committed=kpi_committed,
                    historian_before=historian_before,
                    historian_after=historian_before,
                ),
            )

        write_result = self._history.write(
            evaluations=self._evaluations.iter_between(
                after=historian_before,
                through=kpi_committed,
            ),
            check_cancelled=context.raise_if_cancelled,
        )
        if write_result.last_watermark is None:
            _watermark_failure(
                context,
                reason='committed_evaluation_missing',
                kpi_committed=kpi_committed,
                historian_committed=historian_before,
            )
            raise KpiHistorianWatermarkError(
                'KPI committed watermark has no persisted evaluation available to historian'
            )
        if write_result.last_watermark != kpi_committed:
            _watermark_failure(
                context,
                reason='committed_evaluation_missing',
                kpi_committed=kpi_committed,
                historian_committed=write_result.last_watermark,
            )
            raise KpiHistorianWatermarkError(
                'KPI committed watermark does not match the last persisted evaluation'
            )

        context.raise_if_cancelled()
        historian_after = self._historian_state.commit_watermark(kpi_committed)
        return _record_result(
            context,
            KpiHistorianIterationResult(
                status=KpiHistorianIterationStatus.PROCESSED,
                kpi_committed=kpi_committed,
                historian_before=historian_before,
                historian_after=historian_after,
                evaluations_processed=write_result.evaluation_count,
                history_rows=write_result.history_row_count,
                error_rows=write_result.error_row_count,
                history_publications=write_result.history_publication_count,
                error_publications=write_result.error_publication_count,
            ),
        )


def _record_result(
    context: JobRuntimeContext,
    result: KpiHistorianIterationResult,
) -> KpiHistorianIterationResult:
    outcome = (
        'skipped'
        if result.status
        in {
            KpiHistorianIterationStatus.SKIPPED_NO_KPI_WATERMARK,
            KpiHistorianIterationStatus.SKIPPED_CURRENT,
        }
        else 'completed'
    )
    context.set_iteration_fact('outcome', outcome)
    context.set_iteration_fact('reason', result.status.value)
    if result.kpi_committed is not None:
        context.set_iteration_fact('kpi_committed_watermark_utc', result.kpi_committed.text)
    if result.historian_before is not None:
        context.set_iteration_fact('historian_committed_before_utc', result.historian_before.text)
    if result.historian_after is not None:
        context.set_iteration_fact('historian_committed_after_utc', result.historian_after.text)
    if result.status is KpiHistorianIterationStatus.PROCESSED:
        facts = {
            'evaluations_processed': result.evaluations_processed,
            'history_rows': result.history_rows,
            'error_rows': result.error_rows,
            'history_publications': result.history_publications,
            'error_publications': result.error_publications,
        }
        for key, value in facts.items():
            context.set_iteration_fact(key, value)
            context.increment_execution_counter(key, value)
        context.mark_iteration_work()
    return result


def _watermark_failure(
    context: JobRuntimeContext,
    *,
    reason: str,
    kpi_committed: KpiWatermark | None,
    historian_committed: KpiWatermark | None,
) -> None:
    context.set_iteration_fact('outcome', 'failed')
    context.set_iteration_fact('reason', reason)
    if kpi_committed is not None:
        context.set_iteration_fact('kpi_committed_watermark_utc', kpi_committed.text)
    if historian_committed is not None:
        context.set_iteration_fact('historian_committed_watermark_utc', historian_committed.text)
