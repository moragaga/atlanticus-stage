# El job prepara WebIDs y ExecutionPlan una sola vez en la primera iteración.
# Este incremento todavía solo planifica ventanas; no avanza watermarks ni simula materialización.
# Los hechos de ejecución permiten observar cuánto trabajo de resolución de WebIDs
# ocurrió al arrancar.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ada.processes.pi_web_api.models import (
    PiAcquisitionWindow,
    PiExecutionPlan,
    PiPreparationResult,
)
from ada.processes.pi_web_api.planning import PiSlotPlanner
from ada.processes.pi_web_api.preparation import PiExecutionPlanPreparer
from ada.processes.pi_web_api.watermarks import PiProducerState
from atlanticus.integrations.pi.contracts import PiCatalog
from atlanticus.runtime import JobRuntimeContext


@dataclass(slots=True)
class PiWebApiJob:
    preparer: PiExecutionPlanPreparer
    catalog: PiCatalog
    planner: PiSlotPlanner
    producer_state: PiProducerState
    _preparation: PiPreparationResult | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.preparer, PiExecutionPlanPreparer):
            raise TypeError('preparer must be a PiExecutionPlanPreparer')
        if not isinstance(self.catalog, PiCatalog):
            raise TypeError('catalog must be a PiCatalog')
        if not isinstance(self.planner, PiSlotPlanner):
            raise TypeError('planner must be a PiSlotPlanner')
        if not isinstance(self.producer_state, PiProducerState):
            raise TypeError('producer_state must be a PiProducerState')

    @property
    def preparation(self) -> PiPreparationResult | None:
        return self._preparation

    @property
    def execution_plan(self) -> PiExecutionPlan | None:
        return None if self._preparation is None else self._preparation.plan

    def prepare(self) -> PiPreparationResult:
        if self._preparation is None:
            self._preparation = self.preparer.prepare(self.catalog)
        return self._preparation

    def plan_iteration(self, *, now_utc: datetime) -> PiAcquisitionWindow | None:
        committed = self.producer_state.current().committed_watermark_utc
        return self.planner.plan(
            now_utc=now_utc,
            committed_watermark_utc=committed,
        )

    def run_iteration(self, context: JobRuntimeContext) -> None:
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be a JobRuntimeContext')
        preparation = self.prepare()
        self._initialize_execution_facts(context, preparation)
        window = self.plan_iteration(now_utc=datetime.now(UTC))
        if window is None:
            return
        context.set_iteration_fact('planned_slots', window.slot_count)
        context.set_iteration_fact('recovery_truncated', window.recovery_truncated)

    @staticmethod
    def _initialize_execution_facts(
        context: JobRuntimeContext,
        preparation: PiPreparationResult,
    ) -> None:
        if context.get_execution_fact('resolved_tags') is None:
            context.set_execution_fact('resolved_tags', len(preparation.plan.resolved))
        if context.get_execution_fact('unresolved_tags') is None:
            context.set_execution_fact('unresolved_tags', preparation.unresolved_count)
        if context.get_execution_fact('webid_cache_hits') is None:
            context.set_execution_fact('webid_cache_hits', preparation.cache_hit_count)
        if context.get_execution_fact('webid_resolved') is None:
            context.set_execution_fact('webid_resolved', preparation.resolved_count)
        if context.get_execution_fact('webid_requests') is None:
            context.set_execution_fact('webid_requests', preparation.point_request_count)
