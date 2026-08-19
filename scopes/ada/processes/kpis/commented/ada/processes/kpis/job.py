# Implementa una iteración KPI: compara watermarks, evalúa solo el último tick y confirma el commit.
# Una evaluation ya escrita pero todavía no confirmada se termina de commitear antes de
# calcular un watermark PI posterior. Eso recupera trabajo real; no reconstruye ticks perdidos.
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ada.kpis.core import KpiCatalog, KpiEvaluation, KpiWatermark
from ada.kpis.evaluation import KpiEvaluator
from ada.kpis.persistence import (
    KpiCommitStore,
    KpiEvaluationCommitter,
    KpiEvaluationRepository,
    KpiEvaluationWriteStatus,
)
from ada.processes.kpis.clock import PiClock
from ada.processes.kpis.errors import KpiProcessWatermarkError
from atlanticus.runtime import JobRuntimeContext


class KpiIterationStatus(StrEnum):
    SKIPPED_NO_PI_WATERMARK = 'skipped_no_pi_watermark'
    SKIPPED_CURRENT = 'skipped_current'
    EVALUATED = 'evaluated'
    RETRIED_PERSISTED_EVALUATION = 'retried_persisted_evaluation'


@dataclass(frozen=True, slots=True)
class KpiIterationResult:
    status: KpiIterationStatus
    pi_watermark: KpiWatermark | None
    committed_before: KpiWatermark | None
    committed_after: KpiWatermark | None
    write_status: KpiEvaluationWriteStatus | None = None


class KpiProcessJob:
    def __init__(
        self,
        *,
        catalog: KpiCatalog,
        clock: PiClock,
        evaluator: KpiEvaluator,
        evaluations: KpiEvaluationRepository,
        committer: KpiEvaluationCommitter,
        state: KpiCommitStore,
    ) -> None:
        if not isinstance(catalog, KpiCatalog):
            raise TypeError('catalog must be KpiCatalog')
        if not isinstance(clock, PiClock):
            raise TypeError('clock must implement PiClock')
        if not isinstance(evaluator, KpiEvaluator):
            raise TypeError('evaluator must be KpiEvaluator')
        if not isinstance(evaluations, KpiEvaluationRepository):
            raise TypeError('evaluations must be KpiEvaluationRepository')
        if not isinstance(committer, KpiEvaluationCommitter):
            raise TypeError('committer must be KpiEvaluationCommitter')
        if not isinstance(state, KpiCommitStore):
            raise TypeError('state must be KpiCommitStore')
        self._catalog = catalog
        self._clock = clock
        self._evaluator = evaluator
        self._evaluations = evaluations
        self._committer = committer
        self._state = state

    def run_iteration(self, context: JobRuntimeContext) -> KpiIterationResult:
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be a JobRuntimeContext')
        context.raise_if_cancelled()
        snapshot = self._clock.current()
        committed = self._state.read_watermark()
        target = snapshot.watermark
        if target is None:
            return _record_result(
                context,
                _skipped(
                    status=KpiIterationStatus.SKIPPED_NO_PI_WATERMARK,
                    pi_watermark=None,
                    committed=committed,
                ),
            )
        if committed is not None and target < committed:
            context.set_iteration_fact('outcome', 'failed')
            context.set_iteration_fact('reason', 'pi_watermark_regression')
            context.set_iteration_fact('pi_watermark_utc', target.text)
            context.set_iteration_fact('kpi_committed_watermark_utc', committed.text)
            raise KpiProcessWatermarkError(
                'PI watermark must not be lower than the KPI committed watermark'
            )
        context.set_iteration_fact('pi_observed_watermark_utc', target.text)
        if committed == target:
            return _record_result(
                context,
                _skipped(
                    status=KpiIterationStatus.SKIPPED_CURRENT,
                    pi_watermark=target,
                    committed=committed,
                ),
            )
        pending = next(
            self._evaluations.iter_between(after=committed, through=target),
            None,
        )
        if pending is not None:
            context.raise_if_cancelled()
            return _record_result(
                context,
                self._commit_existing(
                    evaluation=pending,
                    committed_before=committed,
                ),
            )
        context.raise_if_cancelled()
        evaluation = self._evaluator.evaluate(
            catalog=self._catalog,
            watermark=target,
            source_watermarks=snapshot.source_watermarks,
        )
        context.raise_if_cancelled()
        return _record_result(
            context,
            self._commit_new(
                evaluation=evaluation,
                committed_before=committed,
            ),
        )

    def _commit_new(
        self,
        *,
        evaluation: KpiEvaluation,
        committed_before: KpiWatermark | None,
    ) -> KpiIterationResult:
        write_status = self._committer.commit(evaluation)
        return KpiIterationResult(
            status=KpiIterationStatus.EVALUATED,
            pi_watermark=evaluation.watermark,
            committed_before=committed_before,
            committed_after=evaluation.watermark,
            write_status=write_status,
        )

    def _commit_existing(
        self,
        *,
        evaluation: KpiEvaluation,
        committed_before: KpiWatermark | None,
    ) -> KpiIterationResult:
        write_status = self._committer.commit(evaluation)
        return KpiIterationResult(
            status=KpiIterationStatus.RETRIED_PERSISTED_EVALUATION,
            pi_watermark=evaluation.watermark,
            committed_before=committed_before,
            committed_after=evaluation.watermark,
            write_status=write_status,
        )


def _skipped(
    *,
    status: KpiIterationStatus,
    pi_watermark: KpiWatermark | None,
    committed: KpiWatermark | None,
) -> KpiIterationResult:
    return KpiIterationResult(
        status=status,
        pi_watermark=pi_watermark,
        committed_before=committed,
        committed_after=committed,
    )


def _record_result(
    context: JobRuntimeContext,
    result: KpiIterationResult,
) -> KpiIterationResult:
    context.set_iteration_fact('outcome', _outcome(result.status))
    context.set_iteration_fact('reason', result.status.value)
    if result.pi_watermark is not None:
        context.set_iteration_fact('pi_watermark_utc', result.pi_watermark.text)
    if result.committed_before is not None:
        context.set_iteration_fact('kpi_committed_before_utc', result.committed_before.text)
    if result.committed_after is not None:
        context.set_iteration_fact('kpi_committed_after_utc', result.committed_after.text)
    if result.write_status is not None:
        context.set_iteration_fact('evaluation_write_status', result.write_status.value)
    if result.status in {
        KpiIterationStatus.EVALUATED,
        KpiIterationStatus.RETRIED_PERSISTED_EVALUATION,
    }:
        context.mark_iteration_work()
        context.increment_execution_counter('evaluations_committed')
    return result


def _outcome(status: KpiIterationStatus) -> str:
    if status in {
        KpiIterationStatus.SKIPPED_NO_PI_WATERMARK,
        KpiIterationStatus.SKIPPED_CURRENT,
    }:
        return 'skipped'
    return 'completed'
