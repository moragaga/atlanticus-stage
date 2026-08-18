from __future__ import annotations

from atlanticus.data_producers.sql.contracts import SqlSourceExecutor
from atlanticus.data_producers.sql.errors import SqlDataProducerError
from atlanticus.data_producers.sql.models import SqlExecutionPlan, SqlSourceDefinition
from atlanticus.data_producers.sql.planning import SqlDataProducerPlanner
from atlanticus.data_producers.sql.producer_state import SqlProducerState
from atlanticus.runtime import JobRuntimeContext, RuntimeCancellationRequested


class SqlDataProducerJob:
    def __init__(
        self,
        *,
        producer_key: str,
        definitions: tuple[SqlSourceDefinition, ...],
        planner: SqlDataProducerPlanner,
        producer_state: SqlProducerState,
        executor: SqlSourceExecutor,
        missing_scope_fact_name: str = 'missing_scope_values',
    ) -> None:
        if not isinstance(producer_key, str) or not producer_key.strip():
            raise ValueError('producer_key must be a non-empty string')
        if not definitions or not all(
            isinstance(definition, SqlSourceDefinition) for definition in definitions
        ):
            raise ValueError('definitions must contain at least one SqlSourceDefinition')
        if not isinstance(planner, SqlDataProducerPlanner):
            raise TypeError('planner must be a SqlDataProducerPlanner')
        if not isinstance(producer_state, SqlProducerState):
            raise TypeError('producer_state must be a SqlProducerState')
        if not isinstance(executor, SqlSourceExecutor):
            raise TypeError('executor must implement SqlSourceExecutor')
        if not isinstance(missing_scope_fact_name, str) or not missing_scope_fact_name.strip():
            raise ValueError('missing_scope_fact_name must be a non-empty string')
        self._producer_key = producer_key.strip()
        self._definitions = definitions
        self._planner = planner
        self._producer_state = producer_state
        self._executor = executor
        self._missing_scope_fact_name = missing_scope_fact_name.strip()
        prefix = f'{self._producer_key}.execution'
        self._plan_memory_key = f'{prefix}_plan'
        self._cursor_memory_key = f'{prefix}_cursor'
        self._failures_memory_key = f'{prefix}_failures'
        self._first_failure_memory_key = f'{prefix}_first_failure'

    def run_iteration(self, context: JobRuntimeContext) -> None:
        self._initialize_execution_facts(context)
        plan = context.get_or_create(
            self._plan_memory_key,
            lambda: self._planner.capture(self._definitions, context=context),
        )
        self._initialize_plan_facts(context, plan)
        cursor = int(context.get_memory(self._cursor_memory_key, 0) or 0)
        if cursor >= len(plan.sources):
            failures = int(context.get_memory(self._failures_memory_key, 0) or 0)
            if failures:
                self._raise_execution_failure(context, failures)
            context.set_iteration_fact('outcome', 'skipped')
            context.set_iteration_fact('reason', 'no_source_change')
            context.set_next_iteration_delay(context.safe_remaining_seconds)
            return

        source_plan = plan.sources[cursor]
        context.set_memory(self._cursor_memory_key, cursor + 1)
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
            failures = int(context.get_memory(self._failures_memory_key, 0) or 0) + 1
            context.set_memory(self._failures_memory_key, failures)
            if context.get_memory(self._first_failure_memory_key) is None:
                context.set_memory(self._first_failure_memory_key, error)
            context.increment_execution_counter('sources_failed')
            context.set_iteration_fact('outcome', 'skipped')
            context.set_iteration_fact('reason', 'source_failed')
            context.logger.exception(
                f'{self._producer_key} source processing failed',
                error,
                event_name=f'{self._producer_key}.source.failed',
                source=source_plan.definition.source_key,
            )
            if cursor + 1 >= len(plan.sources):
                self._raise_execution_failure(context, failures)
            return

        if result.source_key != source_plan.definition.source_key:
            raise SqlDataProducerError('SQL source executor returned an unexpected source_key')
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
        context.increment_execution_counter('publications_committed', result.publications_committed)
        context.set_iteration_fact('rows_received', result.source_row_count)
        context.set_iteration_fact('rows_materialized', result.rows_published)
        context.set_iteration_fact('publications', result.publication_count)
        context.set_iteration_fact('publications_committed', result.publications_committed)
        context.set_iteration_fact('source_revision', state.revision)
        if result.missing_scope_values:
            context.set_iteration_fact(
                self._missing_scope_fact_name,
                ','.join(str(value) for value in result.missing_scope_values),
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
            failures = int(context.get_memory(self._failures_memory_key, 0) or 0)
            if failures:
                self._raise_execution_failure(context, failures)
            context.set_next_iteration_delay(context.safe_remaining_seconds)

    def _raise_execution_failure(self, context: JobRuntimeContext, failures: int) -> None:
        error = SqlDataProducerError(
            f'{self._producer_key} execution finished with {failures} source failure(s)'
        )
        cause = context.get_memory(self._first_failure_memory_key)
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
    def _initialize_plan_facts(context: JobRuntimeContext, plan: SqlExecutionPlan) -> None:
        if context.get_execution_fact('plan_captured_at_utc') is not None:
            return
        context.set_execution_fact('plan_captured_at_utc', plan.captured_at_utc)
        context.set_execution_fact('sources_planned', len(plan.sources))
