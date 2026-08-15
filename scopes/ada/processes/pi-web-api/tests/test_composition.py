from types import SimpleNamespace

from ada.processes.pi_web_api import PI_WEB_API_JOB_DEFINITION, build_composition
from atlanticus.integrations.pi.web_api import PiPointWebIdResult
from atlanticus.runtime import JobRuntimeContext, RuntimeConfiguration


class FakePoints:
    def __init__(self, owner) -> None:
        self._owner = owner
        self.calls = []

    def resolve_web_ids(self, tag_names):
        assert self._owner.is_open
        self.calls.append(tag_names)
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
    def __init__(self, *, settings) -> None:
        self.settings = settings
        self.is_open = False
        self.open_count = 0
        self.close_count = 0
        self.points = FakePoints(self)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def open(self):
        self.open_count += 1
        self.is_open = True

    def close(self):
        self.close_count += 1
        self.is_open = False


def test_job_definition_prioritizes_freshness_without_extra_pi_requests() -> None:
    assert PI_WEB_API_JOB_DEFINITION.sleep_seconds == 1
    assert PI_WEB_API_JOB_DEFINITION.execution_timeout_seconds == 595
    assert PI_WEB_API_JOB_DEFINITION.shutdown_grace_seconds == 15
    assert PI_WEB_API_JOB_DEFINITION.iteration_timeout_seconds == 240


def test_composition_defers_webid_preparation_until_runtime(
    monkeypatch,
    configuration,
    catalog,
) -> None:
    import ada.processes.pi_web_api.composition as composition_module

    monkeypatch.setattr(composition_module, 'PiWebApiClient', FakeClient)
    composition = build_composition(configuration=configuration, catalog=catalog)

    assert composition.job.preparation is None
    assert composition.client.open_count == 0
    assert composition.client.close_count == 0
    assert composition.planner.interpolation_seconds == 10


def test_execute_keeps_pi_client_open_while_runtime_invokes_job(
    monkeypatch,
    configuration,
    catalog,
) -> None:
    import ada.processes.pi_web_api.composition as composition_module

    sentinel = SimpleNamespace(name='runtime-result')
    monkeypatch.setattr(composition_module, 'PiWebApiClient', FakeClient)

    def fake_execute_job(*, definition, iteration, argv, environ):
        assert definition is PI_WEB_API_JOB_DEFINITION
        assert argv == ('--run-once',)
        runtime_configuration = RuntimeConfiguration.from_sources(environ=environ)
        context = JobRuntimeContext.create(
            definition=definition,
            configuration=runtime_configuration,
            run_id='run-id',
            correlation_id='correlation-id',
        )
        context._begin_iteration(1)
        iteration(context)
        return sentinel

    monkeypatch.setattr(composition_module, 'execute_job', fake_execute_job)
    composition = build_composition(configuration=configuration, catalog=catalog)

    result = composition.execute(argv=('--run-once',))

    assert result is sentinel
    assert composition.client.open_count == 1
    assert composition.client.close_count == 1
    assert composition.client.points.calls == [('TAG_A', 'TAG_B')]
    assert composition.job.preparation is not None
    assert [item.tag_name for item in composition.job.preparation.plan.interpolated] == ['TAG_A']
