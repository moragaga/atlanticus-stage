from datetime import UTC, datetime

from atlanticus.connectivity.sql import SqlTableChangeMarker
from atlanticus.data_producers.sql import (
    SqlDataProducerJob,
    SqlDataProducerPlanner,
    SqlExecutionPlan,
    SqlProducerState,
    SqlSourceExecutionResult,
    SqlSourcePlan,
    SqlSourceState,
)


class _Planner(SqlDataProducerPlanner):
    def __init__(self, plan):
        self.plan = plan
        self.calls = 0

    def capture(self, definitions, *, context=None):
        self.calls += 1
        return self.plan


class _State(SqlProducerState):
    def __init__(self):
        self.values = {}
        self.commits = []

    def source_state(self, source_key):
        return self.values.get(source_key, SqlSourceState(source_key=source_key))

    def commit_source(self, **values):
        self.commits.append(values)
        previous = self.source_state(values['source_key'])
        state = SqlSourceState(
            source_key=values['source_key'],
            revision=previous.revision + (1 if values['changed'] else 0),
            source_change_marker=values['target_change_marker'],
            source_scope_token=values['target_scope_token'],
            publication_signatures=values['publication_signatures'],
        )
        self.values[values['source_key']] = state
        return state


class _Executor:
    def __init__(self, result):
        self.result = result

    def execute(self, *, plan, context):
        return self.result


class _Logger:
    def exception(self, *args, **kwargs):
        return None


class _Context:
    def __init__(self):
        self.memory = {}
        self.execution = {}
        self.iteration = {}
        self.work = False
        self.delay = None
        self.safe_remaining_seconds = 500.0
        self.logger = _Logger()

    def get_or_create(self, key, factory):
        if key not in self.memory:
            self.memory[key] = factory()
        return self.memory[key]

    def get_memory(self, key, default=None):
        return self.memory.get(key, default)

    def set_memory(self, key, value):
        self.memory[key] = value

    def get_execution_fact(self, key, default=None):
        return self.execution.get(key, default)

    def set_execution_fact(self, key, value):
        self.execution[key] = value

    def increment_execution_counter(self, key, amount=1):
        self.execution[key] = self.execution.get(key, 0) + amount

    def set_iteration_fact(self, key, value):
        self.iteration[key] = value

    def mark_iteration_work(self):
        self.work = True

    def set_next_iteration_delay(self, seconds):
        self.delay = seconds


def test_job_commits_marker_even_without_material_change(snapshot_definition) -> None:
    source = SqlSourcePlan(
        definition=snapshot_definition,
        change_marker=SqlTableChangeMarker(
            source_table=snapshot_definition.source_table,
            generation_token='generation',
            last_user_update_token='target',
            user_updates=1,
        ),
    )
    planner = _Planner(
        SqlExecutionPlan(
            captured_at_utc=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
            sources=(source,),
        )
    )
    state = _State()
    job = SqlDataProducerJob(
        producer_key='producer',
        definitions=(snapshot_definition,),
        planner=planner,
        producer_state=state,
        executor=_Executor(
            SqlSourceExecutionResult(
                source_key=snapshot_definition.source_key,
                source_row_count=0,
                publications=(),
            )
        ),
    )
    context = _Context()

    job.run_iteration(context)

    assert planner.calls == 1
    assert state.commits[0]['target_change_marker'].last_user_update_token == 'target'
    assert context.execution['sources_processed'] == 1
    assert context.iteration['reason'] == 'no_material_change'
