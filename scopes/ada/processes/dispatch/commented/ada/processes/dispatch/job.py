# Consume un plan congelado por ejecución y confirma cada fuente de forma independiente.
from __future__ import annotations

from ada.processes.dispatch.contracts import DispatchSourceExecutor
from ada.processes.dispatch.errors import DispatchProcessError
from ada.processes.dispatch.models import DispatchExecutionPlan, DispatchSourceDefinition
from ada.processes.dispatch.planning import DispatchPlanner
from ada.processes.dispatch.producer_state import DispatchProducerState
from atlanticus.runtime import JobRuntimeContext, RuntimeCancellationRequested

_PLAN_MEMORY_KEY = 'dispatch.execution_plan'
_CURSOR_MEMORY_KEY = 'dispatch.execution_cursor'
_FAILURES_MEMORY_KEY = 'dispatch.execution_failures'
_FIRST_FAILURE_MEMORY_KEY = 'dispatch.execution_first_failure'


class DispatchJob:
    def __init__(
        self,
        *,
        definitions: tuple[DispatchSourceDefinition, ...],
        planner: DispatchPlanner,
        producer_state: DispatchProducerState,
        executor: DispatchSourceExecutor,
    ) -> None:
        if not definitions or not all(
            isinstance(definition, DispatchSourceDefinition) for definition in definitions
        ):
            raise ValueError('definitions must contain at least one DispatchSourceDefinition')
        if not isinstance(planner, DispatchPlanner):
            raise TypeError('planner must be a DispatchPlanner')
        if not isinstance(producer_state, DispatchProducerState):
            raise TypeError('producer_state must be a DispatchProducerState')
        if not isinstance(executor, DispatchSourceExecutor):
            raise TypeError('executor must implement DispatchSourceExecutor')
        self._definitions = definitions
        self._planner = planner
        self._producer_state = producer_state
        self._executor = executor

    def run_iteration(self, context: JobRuntimeContext) -> None:
        self._initialize_execution_facts(context)
        plan = context.get_or_create(
            _PLAN_MEMORY_KEY,
            lambda: self._planner.capture(self._definitions, context=context),
        )
        self._initialize_plan_facts(context, plan)
        cursor = int(context.get_memory(_CURSOR_MEMORY_KEY, 0) or 0)
        if cursor >= len(plan.sources):
            failures = int(context.get_memory(_FAILURES_MEMORY_KEY, 0) or 0)
            if failures:
                self._raise_execution_failure(context, failures)
            context.set_iteration_fact('outcome', 'skipped')
            context.set_iteration_fact('reason', 'no_source_change')
            context.set_next_iteration_delay(context.safe_remaining_seconds)
            return

        source_plan = plan.sources[cursor]
        context.set_memory(_CURSOR_MEMORY_KEY, cursor + 1)
        context.set_iteration_fact('source', source_plan.definition.source_key)
        if source_plan.scope_token is not None:
            context.set_iteration_fact('target_scope', source_plan.scope_token)
        if source_plan.change_marker.last_user_update_token is not None:
            context.set_iteration_fact(
                'target_last_user_update',
                source_plan.change_marker.last_user_update_token,
            )
        previous_state = self._producer_state.source_state(source_plan.definition.source_key)
        try:
            result = self._executor.execute(plan=source_plan, context=context)
        except RuntimeCancellationRequested:
            raise
        except Exception as error:
            failures = int(context.get_memory(_FAILURES_MEMORY_KEY, 0) or 0) + 1
            context.set_memory(_FAILURES_MEMORY_KEY, failures)
            if context.get_memory(_FIRST_FAILURE_MEMORY_KEY) is None:
                context.set_memory(_FIRST_FAILURE_MEMORY_KEY, error)
            context.increment_execution_counter('sources_failed')
            context.set_iteration_fact('outcome', 'skipped')
            context.set_iteration_fact('reason', 'source_failed')
            context.logger.exception(
                'Dispatch source processing failed',
                error,
                event_name='dispatch.source.failed',
                source=source_plan.definition.source_key,
            )
            if cursor + 1 >= len(plan.sources):
                self._raise_execution_failure(context, failures)
            return

        if result.source_key != source_plan.definition.source_key:
            raise DispatchProcessError('Dispatch source executor returned an unexpected source_key')
        state = self._producer_state.commit_source(
            source_key=result.source_key,
            target_change_marker=source_plan.change_marker,
            target_scope_token=source_plan.scope_token,
            changed=result.changed,
            source_last_update_utc=result.source_last_update_utc,
            publication_signatures=result.publication_signatures,
        )
        effective_change = state.revision > previous_state.revision
        context.increment_execution_counter('sources_processed')
        context.increment_execution_counter('rows_received', result.source_row_count)
        context.increment_execution_counter('rows_materialized', result.rows_published)
        context.increment_execution_counter('publications', result.publication_count)
        context.increment_execution_counter(
            'publications_committed', result.publications_committed
        )
        context.set_iteration_fact('rows_received', result.source_row_count)
        context.set_iteration_fact('rows_materialized', result.rows_published)
        context.set_iteration_fact('publications', result.publication_count)
        context.set_iteration_fact('publications_committed', result.publications_committed)
        context.set_iteration_fact('source_revision', state.revision)
        if result.missing_shift_ids:
            context.set_iteration_fact(
                'missing_shift_ids',
                ','.join(str(value) for value in result.missing_shift_ids),
            )
        if state.source_last_update_utc is not None:
            context.set_iteration_fact('source_last_update_utc', state.source_last_update_utc)
        if effective_change:
            context.mark_iteration_work()
            context.increment_execution_counter('sources_changed')
            context.set_iteration_fact('outcome', 'completed')
        else:
            context.set_iteration_fact('outcome', 'skipped')
            context.set_iteration_fact('reason', 'no_material_change')
        if cursor + 1 >= len(plan.sources):
            failures = int(context.get_memory(_FAILURES_MEMORY_KEY, 0) or 0)
            if failures:
                self._raise_execution_failure(context, failures)
            context.set_next_iteration_delay(context.safe_remaining_seconds)

    @staticmethod
    def _raise_execution_failure(context: JobRuntimeContext, failures: int) -> None:
        error = DispatchProcessError(
            f'Dispatch execution finished with {failures} source failure(s)'
        )
        cause = context.get_memory(_FIRST_FAILURE_MEMORY_KEY)
        if isinstance(cause, BaseException):
            raise error from cause
        raise error

    @staticmethod
    def _initialize_execution_facts(context: JobRuntimeContext) -> None:
        for key in (
            'sources_planned',
            'sources_processed',
            'sources_changed',
            'sources_failed',
            'rows_received',
            'rows_materialized',
            'publications',
            'publications_committed',
        ):
            if context.get_execution_fact(key) is None:
                context.set_execution_fact(key, 0)

    @staticmethod
    def _initialize_plan_facts(context: JobRuntimeContext, plan: DispatchExecutionPlan) -> None:
        if context.get_execution_fact('plan_captured_at_utc') is not None:
            return
        context.set_execution_fact('plan_captured_at_utc', plan.captured_at_utc)
        context.set_execution_fact('sources_planned', len(plan.sources))
