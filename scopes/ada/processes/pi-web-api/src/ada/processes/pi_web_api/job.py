from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ada.processes.pi_web_api.acquisition import PiStreamSetAcquirer
from ada.processes.pi_web_api.errors import PiWebApiTimeoutExhaustedError
from ada.processes.pi_web_api.materialization import PiWebApiMaterializer
from ada.processes.pi_web_api.models import (
    PiAcquisitionWindow,
    PiExecutionPlan,
    PiPreparationResult,
)
from ada.processes.pi_web_api.planning import PiSlotPlanner
from ada.processes.pi_web_api.preparation import PiExecutionPlanPreparer
from ada.processes.pi_web_api.watermarks import PiProducerState, PiWatermarkCoordinator
from atlanticus.datasets import PublicationStatus
from atlanticus.integrations.pi.contracts import PiCatalog
from atlanticus.runtime import JobRuntimeContext


@dataclass(slots=True)
class PiWebApiJob:
    preparer: PiExecutionPlanPreparer
    catalog: PiCatalog
    planner: PiSlotPlanner
    producer_state: PiProducerState
    acquirer: PiStreamSetAcquirer
    materializer: PiWebApiMaterializer
    watermarks: PiWatermarkCoordinator
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
        if not isinstance(self.acquirer, PiStreamSetAcquirer):
            raise TypeError('acquirer must be a PiStreamSetAcquirer')
        if not isinstance(self.materializer, PiWebApiMaterializer):
            raise TypeError('materializer must be a PiWebApiMaterializer')
        if not isinstance(self.watermarks, PiWatermarkCoordinator):
            raise TypeError('watermarks must be a PiWatermarkCoordinator')

    @property
    def preparation(self) -> PiPreparationResult | None:
        return self._preparation

    @property
    def execution_plan(self) -> PiExecutionPlan | None:
        return None if self._preparation is None else self._preparation.plan

    def prepare(
        self,
        *,
        context: JobRuntimeContext | None = None,
    ) -> PiPreparationResult:
        if self._preparation is None:
            self._preparation = self.preparer.prepare(self.catalog, context=context)
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
        try:
            preparation = self.prepare(context=context)
        except PiWebApiTimeoutExhaustedError as error:
            context.mark_iteration_work()
            self._record_timeout_skip(context=context, window=None, error=error)
            context.set_next_iteration_delay(0)
            return
        self._initialize_execution_facts(context, preparation)
        planning_now = datetime.now(UTC)
        window = self.plan_iteration(now_utc=planning_now)
        if window is None:
            context.set_iteration_fact('outcome', 'skipped')
            context.set_iteration_fact('reason', 'no_new_slot')
            self._schedule_next_iteration(
                context=context,
                now_utc=planning_now,
                committed_watermark_utc=self.producer_state.current().committed_watermark_utc,
            )
            return

        context.mark_iteration_work()
        context.set_iteration_fact('planned_slots', window.slot_count)
        context.set_iteration_fact('recovery_truncated', window.recovery_truncated)
        if window.recovery_truncated:
            context.logger.warning(
                'PI recovery was truncated to the configured lookback horizon',
                event_name='pi_web_api.recovery.truncated',
                first_slot_utc=window.first_slot_utc,
                last_slot_utc=window.last_slot_utc,
                slot_count=window.slot_count,
            )

        try:
            acquisition = self.acquirer.acquire(
                plan=preparation.plan,
                window=window,
                context=context,
            )
        except PiWebApiTimeoutExhaustedError as error:
            self._record_timeout_skip(context=context, window=window, error=error)
            context.set_next_iteration_delay(0)
            return
        self._record_timeout_recovery(context=context, window=window)
        context.raise_if_cancelled()
        materialization = self.materializer.publish(
            window=window,
            acquisition=acquisition,
            context=context,
        )
        if materialization.recorded_second_conflict_count:
            context.logger.warning(
                'Multiple recorded PI events for the same tag occurred within one second',
                event_name='pi_web_api.recorded.second_conflict',
                conflict_count=materialization.recorded_second_conflict_count,
            )
        context.raise_if_cancelled()
        source, producer = self.watermarks.commit_materialized(window.last_slot_utc)

        committed_publications = sum(
            publication.status is PublicationStatus.COMMITTED
            for publication in materialization.publications
        )
        self._set_iteration_facts(
            context=context,
            window=window,
            acquisition=acquisition,
            publication_count=len(materialization.publications),
            committed_publications=committed_publications,
            recorded_second_conflicts=materialization.recorded_second_conflict_count,
            source_watermark=source.source_watermark_utc,
            producer_watermark=producer.committed_watermark_utc,
        )
        committed_at = datetime.now(UTC)
        if window.slot_count == 1:
            context.set_iteration_fact(
                'slot_commit_latency_seconds',
                max(0.0, (committed_at - window.last_slot_utc).total_seconds()),
            )
        self._schedule_next_iteration(
            context=context,
            now_utc=committed_at,
            committed_watermark_utc=producer.committed_watermark_utc,
        )

    def _schedule_next_iteration(
        self,
        *,
        context: JobRuntimeContext,
        now_utc: datetime,
        committed_watermark_utc: datetime | None,
    ) -> None:
        next_wake = self.planner.next_wake_at(
            now_utc=now_utc,
            committed_watermark_utc=committed_watermark_utc,
        )
        delay_seconds = max(0.0, (next_wake - now_utc).total_seconds())
        context.set_next_iteration_delay(delay_seconds)
        context.set_iteration_fact('next_wake_utc', next_wake)
        context.set_iteration_fact('next_iteration_delay_seconds', delay_seconds)

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
        for key in (
            'pi_requests',
            'slots_processed',
            'publications_committed',
            'pi_timeout_skips',
            'pi_timeout_retries',
            'webid_timeout_requests',
        ):
            if context.get_execution_fact(key) is None:
                context.set_execution_fact(key, 0)

    @staticmethod
    def _record_timeout_skip(
        *,
        context: JobRuntimeContext,
        window: PiAcquisitionWindow | None,
        error: PiWebApiTimeoutExhaustedError,
    ) -> None:
        consecutive = int(context.get_memory('pi_timeout_consecutive_skips', 0) or 0) + 1
        context.set_memory('pi_timeout_consecutive_skips', consecutive)
        context.set_iteration_fact('outcome', 'skipped')
        context.set_iteration_fact('reason', 'pi_timeout')
        context.set_iteration_fact('planned_slots', 0 if window is None else window.slot_count)
        context.set_iteration_fact('timeout_phase', error.phase)
        context.set_iteration_fact('timeout_retries', error.retry_count)
        context.set_iteration_fact('pi_requests', error.request_count)
        context.set_iteration_fact('point_requests', error.point_request_count)
        context.set_iteration_fact('interpolated_requests', error.interpolated_request_count)
        context.set_iteration_fact('recorded_requests', error.recorded_request_count)
        context.set_iteration_fact('window_splits', error.split_count)
        context.set_iteration_fact('consecutive_timeout_skips', consecutive)
        context.increment_execution_counter('pi_requests', error.request_count)
        context.increment_execution_counter('webid_timeout_requests', error.point_request_count)
        context.increment_execution_counter('pi_timeout_skips')
        context.logger.warning(
            'PI Web API iteration was skipped after timeout retries',
            event_name='pi_web_api.timeout.iteration_skipped',
            phase=error.phase,
            retry_count=error.retry_count,
            consecutive_timeout_skips=consecutive,
            point_requests=error.point_request_count,
            pi_requests=error.request_count,
            interpolated_requests=error.interpolated_request_count,
            recorded_requests=error.recorded_request_count,
            window_splits=error.split_count,
            planned_slots=0 if window is None else window.slot_count,
            first_slot_utc=None if window is None else window.first_slot_utc,
            last_slot_utc=None if window is None else window.last_slot_utc,
        )

    @staticmethod
    def _record_timeout_recovery(
        *,
        context: JobRuntimeContext,
        window: PiAcquisitionWindow,
    ) -> None:
        consecutive = int(context.get_memory('pi_timeout_consecutive_skips', 0) or 0)
        if consecutive <= 0:
            return
        context.set_memory('pi_timeout_consecutive_skips', 0)
        context.logger.info(
            'PI Web API acquisition recovered after timeout-skipped iterations',
            event_name='pi_web_api.timeout.recovered',
            previous_timeout_iterations=consecutive,
            recovered_slots=window.slot_count,
            first_slot_utc=window.first_slot_utc,
            last_slot_utc=window.last_slot_utc,
        )

    @staticmethod
    def _set_iteration_facts(
        *,
        context: JobRuntimeContext,
        window: PiAcquisitionWindow,
        acquisition,
        publication_count: int,
        committed_publications: int,
        recorded_second_conflicts: int,
        source_watermark: datetime | None,
        producer_watermark: datetime | None,
    ) -> None:
        context.set_iteration_fact('outcome', 'completed')
        context.set_iteration_fact('pi_requests', acquisition.request_count)
        context.set_iteration_fact('interpolated_requests', acquisition.interpolated_request_count)
        context.set_iteration_fact('recorded_requests', acquisition.recorded_request_count)
        context.set_iteration_fact('window_splits', acquisition.split_count)
        context.set_iteration_fact('publications', publication_count)
        context.set_iteration_fact('publications_committed', committed_publications)
        context.set_iteration_fact('recorded_second_conflicts', recorded_second_conflicts)
        context.set_iteration_fact('recorded_exact_conflicts', acquisition.recorded_conflict_count)
        context.set_iteration_fact(
            'interpolated_conflicts', acquisition.interpolated_conflict_count
        )
        if source_watermark is not None:
            context.set_iteration_fact('source_watermark_utc', source_watermark)
            context.set_execution_fact('source_watermark_utc', source_watermark)
        if producer_watermark is not None:
            context.set_iteration_fact('producer_watermark_utc', producer_watermark)
            context.set_execution_fact('producer_watermark_utc', producer_watermark)
        context.increment_execution_counter('pi_requests', acquisition.request_count)
        context.increment_execution_counter('slots_processed', window.slot_count)
        context.increment_execution_counter('publications_committed', committed_publications)
