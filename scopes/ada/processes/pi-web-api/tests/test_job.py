from datetime import UTC, datetime
from types import SimpleNamespace

from ada.processes.pi_web_api import (
    PiExecutionPlanPreparer,
    PiProducerState,
    PiSlotPlanner,
    PiWebApiJob,
    WebIdRegistry,
)
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


class FakeClient:
    def __init__(self) -> None:
        self.points = FakePoints()
        self.settings = SimpleNamespace(limits=PiWebApiLimits())


def _job(tmp_path, catalog):
    producer_state = PiProducerState(
        store=AtomicStateStore(volume_path=tmp_path, application='ada')
    )
    client = FakeClient()
    job = PiWebApiJob(
        preparer=PiExecutionPlanPreparer(
            client=client,
            registry=WebIdRegistry(path=tmp_path / 'webids.json'),
        ),
        catalog=catalog,
        planner=PiSlotPlanner(interpolation_seconds=10),
        producer_state=producer_state,
    )
    return job, producer_state, client


def test_job_prepares_execution_plan_once(tmp_path, catalog) -> None:
    job, _, client = _job(tmp_path, catalog)

    first = job.prepare()
    second = job.prepare()

    assert first is second
    assert client.points.calls == 1
    assert [item.tag_name for item in first.plan.interpolated] == ['TAG_A']
    assert [item.tag_name for item in first.plan.recorded] == ['TAG_B']


def test_job_plans_from_private_committed_watermark(tmp_path, catalog) -> None:
    job, producer_state, _ = _job(tmp_path, catalog)

    first = job.plan_iteration(now_utc=datetime(2026, 8, 14, 10, 9, 24, tzinfo=UTC))
    assert first is not None
    assert first.first_slot_utc == datetime(2026, 8, 14, 10, 9, 20, tzinfo=UTC)

    producer_state.commit(datetime(2026, 8, 14, 10, 9, 20, tzinfo=UTC))

    assert job.plan_iteration(now_utc=datetime(2026, 8, 14, 10, 9, 24, tzinfo=UTC)) is None
    next_window = job.plan_iteration(now_utc=datetime(2026, 8, 14, 10, 9, 31, tzinfo=UTC))
    assert next_window is not None
    assert next_window.first_slot_utc == datetime(2026, 8, 14, 10, 9, 30, tzinfo=UTC)


def test_run_iteration_prepares_once_without_advancing_watermark(tmp_path, catalog) -> None:
    job, producer_state, client = _job(tmp_path, catalog)
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

    job.run_iteration(context)
    job.run_iteration(context)

    assert client.points.calls == 1
    assert producer_state.current().committed_watermark_utc is None
    assert context.get_execution_fact('resolved_tags') == 2
    assert context.get_execution_fact('unresolved_tags') == 0
