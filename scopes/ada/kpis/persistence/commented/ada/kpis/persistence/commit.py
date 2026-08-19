# Coordina el orden crítico: evaluation -> latest -> committed watermark.
from __future__ import annotations

from ada.kpis.core import KpiEvaluation
from ada.kpis.persistence.models import KpiEvaluationWriteStatus
from ada.kpis.persistence.repositories import KpiEvaluationRepository, KpiLatestRepository
from ada.kpis.persistence.state import KpiCommitStore


class KpiEvaluationCommitter:
    def __init__(
        self,
        *,
        evaluations: KpiEvaluationRepository,
        latest: KpiLatestRepository,
        state: KpiCommitStore,
    ) -> None:
        if not isinstance(evaluations, KpiEvaluationRepository):
            raise TypeError('evaluations must be KpiEvaluationRepository')
        if not isinstance(latest, KpiLatestRepository):
            raise TypeError('latest must be KpiLatestRepository')
        if not isinstance(state, KpiCommitStore):
            raise TypeError('state must be KpiCommitStore')
        self._evaluations = evaluations
        self._latest = latest
        self._state = state

    def commit(self, evaluation: KpiEvaluation) -> KpiEvaluationWriteStatus:
        if not isinstance(evaluation, KpiEvaluation):
            raise TypeError('evaluation must be KpiEvaluation')
        status = self._evaluations.write_once(evaluation)
        self._latest.replace(evaluation)
        self._state.commit_watermark(evaluation.watermark)
        return status
