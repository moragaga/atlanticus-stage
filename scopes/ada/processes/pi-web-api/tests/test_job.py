from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ada.processes.pi_web_api import (
    PiExecutionPlanPreparer,
    PiProducerState,
    PiSlotPlanner,
    PiSourceState,
    PiStreamSetAcquirer,
    PiWatermarkCoordinator,
    PiWebApiJob,
    PiWebApiMaterializer,
    PiWebApiTimeoutExhaustedError,
    WebIdRegistry,
)
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.integrations.pi.web_api import PiPointWebIdResult, PiWebApiLimits
from atlanticus.runtime import JobDefinition, JobRuntimeContext, RuntimeConfiguration
from atlanticus.state import AtomicStateStore


class FakePoints:
    def __init__(self) -> None:
        self.calls = 0

    def resolve_web_ids(self, tag_names):
        self.calls += 1
        return tuple(
            PiPointWebIdResult(
                tag_name=tag_name,
                path=f'\\\\PISERVER\\{tag_name}',
                point_name=tag_name,
                web_id=f'WEB_{tag_name}',
                error=None,
            )
            for tag_name in tag_names
        )


class FakeStreamSets:
    def __init__(self) -> None:
        self.interpolated_calls = 0
        self.recorded_calls = 0

    def get_interpolated(
        self,
        web_ids,
        *,
        start_time_utc,
        end_time_utc,
        interpolation_seconds,
    ):
        self.interpolated_calls += 1
        return (
            {
                'name': 'TAG_A',
                'timestamp': start_time_utc.isoformat(),
                'value': 1.0,
            },
        )

    def get_recorded(self, web_ids, *, start_time_utc, end_time_utc):
        self.recorded_calls += 1
        return (
            {
                'name': 'TAG_B',
                'timestamp': (start_time_utc.replace(microsecond=0)).isoformat(),
                'value': 'RUNNING',
            },
        )


class FakeClient:
    def __init__(self) -> None:
        self.points = FakePoints()
        self.streamsets = FakeStreamSets()
        self.settings = SimpleNamespace(limits=PiWebApiLimits())


def _job(tmp_path, catalog):
    state_store = AtomicStateStore(volume_path=tmp_path, application='ada')
    producer_state = PiProducerState(store=state_store)
    source_state = PiSourceState(store=state_store)
    watermarks = PiWatermarkCoordinator(producer=producer_state, source=source_state)
    client = FakeClient()
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / 'ada' / 'datasets'))
    job = PiWebApiJob(
        preparer=PiExecutionPlanPreparer(
            client=client,
            registry=WebIdRegistry(path=tmp_path / 'webids.json'),
        ),
        catalog=catalog,
        planner=PiSlotPlanner(interpolation_seconds=10),
        producer_state=producer_state,
        acquirer=PiStreamSetAcquirer(client=client),
        materializer=PiWebApiMaterializer(runtime=runtime, catalog=catalog),
        watermarks=watermarks,
    )
    return job, producer_state, source_state, client


def _context(tmp_path) -> JobRuntimeContext:
    definition = JobDefinition(
        module_name='ada.processes.pi_web_api',
        service_name='pi-web-api',
        execution_timeout_seconds=10,
        shutdown_grace_seconds=1,
        iteration_timeout_seconds=5,
    )
    runtime_configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=runtime_configuration,
        run_id='run-id',
        correlation_id='correlation-id',
    )
    context._begin_iteration(1)
    return context


def test_job_prepares_execution_plan_once(tmp_path, catalog) -> None:
    job, _, _, client = _job(tmp_path, catalog)

    first = job.prepare()
    second = job.prepare()

    assert first is second
    assert client.points.calls == 1
    assert [item.tag_name for item in first.plan.interpolated] == ['TAG_A']
    assert [item.tag_name for item in first.plan.recorded] == ['TAG_B']


def test_job_plans_from_private_committed_watermark(tmp_path, catalog) -> None:
    job, producer_state, _, _ = _job(tmp_path, catalog)

    first = job.plan_iteration(now_utc=datetime(2026, 8, 14, 10, 9, 24, tzinfo=UTC))
    assert first is not None
    assert first.first_slot_utc == datetime(2026, 8, 14, 10, 9, 20, tzinfo=UTC)

    producer_state.commit(datetime(2026, 8, 14, 10, 9, 20, tzinfo=UTC))

    assert job.plan_iteration(now_utc=datetime(2026, 8, 14, 10, 9, 24, tzinfo=UTC)) is None
    next_window = job.plan_iteration(now_utc=datetime(2026, 8, 14, 10, 9, 31, tzinfo=UTC))
    assert next_window is not None
    assert next_window.first_slot_utc == datetime(2026, 8, 14, 10, 9, 30, tzinfo=UTC)


def test_run_iteration_materializes_then_commits_public_and_private_watermarks(
    tmp_path,
    catalog,
) -> None:
    job, producer_state, source_state, client = _job(tmp_path, catalog)
    context = _context(tmp_path)

    job.run_iteration(context)

    producer = producer_state.current().committed_watermark_utc
    source = source_state.current().source_watermark_utc
    assert producer is not None
    assert source == producer
    assert client.points.calls == 1
    assert client.streamsets.interpolated_calls == 1
    assert client.streamsets.recorded_calls == 1
    assert context.get_iteration_fact('outcome') == 'completed'
    assert context.get_execution_fact('pi_requests') == 2
    assert context.get_iteration_fact('next_wake_utc') is not None
    assert 0 <= context._next_iteration_delay() <= 10
    assert context.get_iteration_fact('slot_commit_latency_seconds') is not None


def test_job_does_not_advance_watermarks_when_materialization_fails(
    tmp_path,
    catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, producer_state, source_state, _ = _job(tmp_path, catalog)
    context = _context(tmp_path)

    def fail_publish(*args, **kwargs):
        raise RuntimeError('controlled materialization failure')

    monkeypatch.setattr(job.materializer, 'publish', fail_publish)

    with pytest.raises(RuntimeError, match='controlled materialization failure'):
        job.run_iteration(context)

    assert producer_state.current().committed_watermark_utc is None
    assert source_state.current().source_watermark_utc is None


def test_timeout_exhaustion_skips_iteration_without_advancing_watermarks_and_recovers(
    tmp_path,
    catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, producer_state, source_state, client = _job(tmp_path, catalog)
    context = _context(tmp_path)
    original_acquire = job.acquirer.acquire
    calls = 0

    def flaky_acquire(*, plan, window, context):
        nonlocal calls
        calls += 1
        if calls == 1:
            context.increment_execution_counter('pi_timeout_retries', 3)
            raise PiWebApiTimeoutExhaustedError(
                phase='connect',
                retry_count=3,
                interpolated_request_count=4,
                recorded_request_count=0,
                split_count=0,
            )
        return original_acquire(plan=plan, window=window, context=context)

    monkeypatch.setattr(job.acquirer, 'acquire', flaky_acquire)

    job.run_iteration(context)

    assert producer_state.current().committed_watermark_utc is None
    assert source_state.current().source_watermark_utc is None
    assert context.get_iteration_fact('outcome') == 'skipped'
    assert context.get_iteration_fact('reason') == 'pi_timeout'
    assert context.get_iteration_fact('timeout_phase') == 'connect'
    assert context.get_iteration_fact('timeout_retries') == 3
    assert context.get_iteration_fact('pi_requests') == 4
    assert context.get_iteration_fact('consecutive_timeout_skips') == 1
    assert context.get_execution_fact('pi_timeout_skips') == 1
    assert context.get_execution_fact('pi_timeout_retries') == 3
    assert context.get_execution_fact('pi_requests') == 4
    assert context.should_stop is False
    assert context._next_iteration_delay() == 0

    context._begin_iteration(2)
    job.run_iteration(context)

    producer = producer_state.current().committed_watermark_utc
    source = source_state.current().source_watermark_utc
    assert producer is not None
    assert source == producer
    assert context.get_iteration_fact('outcome') == 'completed'
    assert context.get_memory('pi_timeout_consecutive_skips') == 0
    assert context.get_execution_fact('pi_timeout_skips') == 1
    assert context.get_execution_fact('pi_timeout_retries') == 3
    assert context.get_execution_fact('pi_requests') == 6
    assert client.streamsets.interpolated_calls == 1
    assert client.streamsets.recorded_calls == 1


def test_webid_timeout_exhaustion_skips_iteration_before_planning(
    tmp_path,
    catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, producer_state, source_state, _ = _job(tmp_path, catalog)
    context = _context(tmp_path)

    def fail_prepare(catalog, *, context=None):
        context.increment_execution_counter('pi_timeout_retries', 3)
        raise PiWebApiTimeoutExhaustedError(
            phase='connect',
            retry_count=3,
            point_request_count=4,
        )

    monkeypatch.setattr(job.preparer, 'prepare', fail_prepare)

    job.run_iteration(context)

    assert producer_state.current().committed_watermark_utc is None
    assert source_state.current().source_watermark_utc is None
    assert context.get_iteration_fact('outcome') == 'skipped'
    assert context.get_iteration_fact('reason') == 'pi_timeout'
    assert context.get_iteration_fact('planned_slots') == 0
    assert context.get_iteration_fact('point_requests') == 4
    assert context.get_iteration_fact('pi_requests') == 0
    assert context.get_execution_fact('webid_timeout_requests') == 4
    assert context.get_execution_fact('pi_timeout_skips') == 1
    assert context.get_execution_fact('pi_timeout_retries') == 3
    assert context.should_stop is False
    assert context._next_iteration_delay() == 0


def test_caught_up_job_skips_pi_and_schedules_exact_next_boundary(tmp_path, catalog) -> None:
    job, _, _, client = _job(tmp_path, catalog)
    context = _context(tmp_path)

    job.run_iteration(context)
    first_interpolated_calls = client.streamsets.interpolated_calls
    first_recorded_calls = client.streamsets.recorded_calls

    context._begin_iteration(2)
    job.run_iteration(context)

    assert context.get_iteration_fact('outcome') == 'skipped'
    assert context.get_iteration_fact('reason') == 'no_new_slot'
    assert client.streamsets.interpolated_calls == first_interpolated_calls
    assert client.streamsets.recorded_calls == first_recorded_calls
    next_wake = context.get_iteration_fact('next_wake_utc')
    assert isinstance(next_wake, datetime)
    assert next_wake.microsecond == 0
    assert next_wake.second % 10 == 0
    assert 0 <= context._next_iteration_delay() <= 10
