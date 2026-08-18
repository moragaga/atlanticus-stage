from datetime import UTC, datetime

import pytest

from ada.processes.dispatch.errors import DispatchProcessError
from ada.processes.dispatch.job import DispatchJob
from ada.processes.dispatch.models import (
    DispatchExecutionPlan,
    DispatchPublicationResult,
    DispatchSourceExecutionResult,
    DispatchSourcePlan,
)
from ada.processes.dispatch.planning import DispatchPlanner
from ada.processes.dispatch.producer_state import DispatchProducerState, DispatchSourceState
from atlanticus.connectivity.sql import SqlTableChangeMarker
from atlanticus.datasets.models import DatasetKey, DatasetTarget
from atlanticus.datasets.results import (
    DatasetPublicationResult,
    PublicationQuality,
    PublicationStatus,
)
from atlanticus.runtime import RuntimeCancellationRequested


class _Planner(DispatchPlanner):
    def __init__(self, plan):
        self.plan = plan
        self.calls = 0

    def capture(self, definitions, *, context=None):
        self.calls += 1
        return self.plan


class _State(DispatchProducerState):
    def __init__(self):
        self.values = {}
        self.commits = []

    def source_state(self, source_key):
        return self.values.get(source_key, DispatchSourceState(source_key=source_key))

    def commit_source(self, **values):
        self.commits.append(values)
        previous = self.source_state(values['source_key'])
        revision = previous.revision + (1 if values['changed'] else 0)
        state = DispatchSourceState(
            source_key=values['source_key'],
            revision=revision,
            source_change_marker=values['target_change_marker'],
            source_scope_token=values['target_scope_token'],
            source_last_update_utc=values['source_last_update_utc'],
            publication_signatures=values['publication_signatures'],
        )
        self.values[values['source_key']] = state
        return state


class _Executor:
    def __init__(self, results):
        self.results = iter(results)
        self.sources = []

    def execute(self, *, plan, context):
        self.sources.append(plan.definition.source_key)
        value = next(self.results)
        if isinstance(value, Exception):
            raise value
        return value


class _Logger:
    def error(self, *args, **kwargs):
        return None

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


def _source(definition) -> DispatchSourcePlan:
    return DispatchSourcePlan(
        definition=definition,
        change_marker=SqlTableChangeMarker(
            source_table=definition.source_table,
            generation_token='generation',
            last_user_update_token=definition.source_key,
            user_updates=1,
        ),
        scope_token=None,
    )


def _committed(source_key: str) -> DispatchSourceExecutionResult:
    target = DatasetTarget(
        dataset=DatasetKey(namespace=('dispatch',), name=source_key), materialization='latest'
    )
    publication = DatasetPublicationResult(
        target=target,
        status=PublicationStatus.COMMITTED,
        quality=PublicationQuality.SUCCESS,
        finished_at_utc=datetime(2026, 8, 17, 22, 0, tzinfo=UTC),
        duration_ms=1.0,
        item_count=1,
        artifact_count=1,
        size_bytes=10,
        content_signature='signature',
    )
    return DispatchSourceExecutionResult(
        source_key=source_key,
        source_row_count=1,
        publications=(DispatchPublicationResult(publication=publication),),
    )


def test_job_uses_one_plan_and_commits_sources_independently(snapshot_definition) -> None:
    second = type(snapshot_definition)(
        source_key='source_b',
        source_table='dbo.source_b',
        storage_mode=snapshot_definition.storage_mode,
        load_strategy=snapshot_definition.load_strategy,
        columns=snapshot_definition.columns,
    )
    sources = (_source(snapshot_definition), _source(second))
    planner = _Planner(
        DispatchExecutionPlan(
            captured_at_utc=datetime(2026, 8, 17, 22, 0, tzinfo=UTC),
            sources=sources,
        )
    )
    state = _State()
    executor = _Executor(
        (
            _committed('source_latest'),
            DispatchSourceExecutionResult(
                source_key='source_b', source_row_count=0, publications=()
            ),
        )
    )
    job = DispatchJob(
        definitions=tuple(source.definition for source in sources),
        planner=planner,
        producer_state=state,
        executor=executor,
    )
    context = _Context()

    job.run_iteration(context)
    assert 'target_scope' not in context.iteration
    assert context.iteration['target_last_user_update'] == 'source_latest'
    context.iteration = {}
    context.work = False
    job.run_iteration(context)

    assert planner.calls == 1
    assert executor.sources == ['source_latest', 'source_b']
    assert [item['source_key'] for item in state.commits] == ['source_latest', 'source_b']
    assert context.execution['sources_processed'] == 2
    assert context.execution['sources_changed'] == 1
    assert context.execution['publications_committed'] == 1
    assert context.delay == context.safe_remaining_seconds


def test_failed_source_does_not_block_next_source(snapshot_definition) -> None:
    second = type(snapshot_definition)(
        source_key='source_b',
        source_table='dbo.source_b',
        storage_mode=snapshot_definition.storage_mode,
        load_strategy=snapshot_definition.load_strategy,
        columns=snapshot_definition.columns,
    )
    sources = (_source(snapshot_definition), _source(second))
    planner = _Planner(
        DispatchExecutionPlan(
            captured_at_utc=datetime(2026, 8, 17, 22, 0, tzinfo=UTC),
            sources=sources,
        )
    )
    state = _State()
    executor = _Executor((RuntimeError('failure'), _committed('source_b')))
    job = DispatchJob(
        definitions=tuple(source.definition for source in sources),
        planner=planner,
        producer_state=state,
        executor=executor,
    )
    context = _Context()

    job.run_iteration(context)
    context.iteration = {}
    with pytest.raises(DispatchProcessError, match='1 source failure'):
        job.run_iteration(context)

    assert executor.sources == ['source_latest', 'source_b']
    assert [item['source_key'] for item in state.commits] == ['source_b']
    assert context.execution['sources_failed'] == 1


def test_runtime_cancellation_is_not_converted_to_source_failure(snapshot_definition) -> None:
    source = _source(snapshot_definition)
    planner = _Planner(
        DispatchExecutionPlan(
            captured_at_utc=datetime(2026, 8, 17, 22, 0, tzinfo=UTC),
            sources=(source,),
        )
    )
    state = _State()
    executor = _Executor((RuntimeCancellationRequested('requested'),))
    job = DispatchJob(
        definitions=(source.definition,),
        planner=planner,
        producer_state=state,
        executor=executor,
    )
    context = _Context()

    with pytest.raises(RuntimeCancellationRequested, match='requested'):
        job.run_iteration(context)

    assert context.execution['sources_failed'] == 0
    assert state.commits == []
