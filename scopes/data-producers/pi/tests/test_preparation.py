from types import SimpleNamespace

import pytest

import atlanticus.data_producers.pi.timeout_retry as timeout_retry_module
from atlanticus.data_producers.pi import (
    PiDataProducerCatalogError,
    PiDataProducerTimeoutExhaustedError,
    PiExecutionPlanPreparer,
    WebIdRegistry,
)
from atlanticus.integrations.pi.contracts import (
    NotPiiSource,
    PiCatalog,
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
)
from atlanticus.integrations.pi.web_api import (
    PiPointWebIdResult,
    PiWebApiLimits,
    PiWebApiTimeoutError,
)
from atlanticus.runtime import JobDefinition, JobRuntimeContext, RuntimeConfiguration


class FakePoints:
    def __init__(self, web_ids: dict[str, str]) -> None:
        self._web_ids = web_ids
        self.calls: list[tuple[str, ...]] = []

    def resolve_web_ids(self, tag_names: tuple[str, ...]) -> tuple[PiPointWebIdResult, ...]:
        self.calls.append(tag_names)
        return tuple(
            PiPointWebIdResult(
                tag_name=tag_name,
                path=f'\\\\PISERVER\\{tag_name}',
                point_name=tag_name if tag_name in self._web_ids else None,
                web_id=self._web_ids.get(tag_name),
                error=None if tag_name in self._web_ids else 'Point not found',
            )
            for tag_name in tag_names
        )


class FakeClient:
    def __init__(self, web_ids: dict[str, str], *, limit: int = 100) -> None:
        self.points = FakePoints(web_ids)
        self.settings = SimpleNamespace(
            limits=PiWebApiLimits(
                points_max_paths=limit,
                interpolated_max_web_ids=100,
                recorded_max_web_ids=100,
            )
        )


def _context(tmp_path) -> JobRuntimeContext:
    definition = JobDefinition(
        module_name='tests.pi_preparation',
        service_name='pi-web-api',
        execution_timeout_seconds=30,
        shutdown_grace_seconds=1,
        iteration_timeout_seconds=10,
    )
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='run-id',
        correlation_id='correlation-id',
    )
    context._begin_iteration(1)
    return context


def test_prepare_reuses_cache_and_resolves_only_missing_tags(tmp_path, catalog) -> None:
    registry = WebIdRegistry(path=tmp_path / 'webids.json')
    registry.merge({'TAG_A': 'WEB_A', 'TAG_UNUSED': 'WEB_UNUSED'})
    client = FakeClient({'TAG_B': 'WEB_B'})

    result = PiExecutionPlanPreparer(client=client, registry=registry).prepare(catalog)

    assert client.points.calls == [('TAG_B',)]
    assert result.cache_hit_count == 1
    assert result.resolved_count == 1
    assert result.unresolved_count == 0
    assert result.point_request_count == 1
    assert [item.tag_name for item in result.plan.interpolated] == ['TAG_A']
    assert [item.tag_name for item in result.plan.recorded] == ['TAG_B']
    assert dict(registry.current()) == {
        'TAG_A': 'WEB_A',
        'TAG_B': 'WEB_B',
        'TAG_UNUSED': 'WEB_UNUSED',
    }


def test_prepare_with_complete_cache_makes_no_point_requests(tmp_path, catalog) -> None:
    registry = WebIdRegistry(path=tmp_path / 'webids.json')
    registry.merge({'TAG_A': 'WEB_A', 'TAG_B': 'WEB_B'})
    client = FakeClient({})

    result = PiExecutionPlanPreparer(client=client, registry=registry).prepare(catalog)

    assert client.points.calls == []
    assert result.cache_hit_count == 2
    assert result.resolved_count == 0
    assert result.point_request_count == 0


def test_prepare_keeps_running_when_a_new_tag_cannot_be_resolved(tmp_path, catalog) -> None:
    registry = WebIdRegistry(path=tmp_path / 'webids.json')
    registry.merge({'TAG_A': 'WEB_A'})
    client = FakeClient({})

    result = PiExecutionPlanPreparer(client=client, registry=registry).prepare(catalog)

    assert [item.tag_name for item in result.plan.interpolated] == ['TAG_A']
    assert result.plan.recorded == ()
    assert result.plan.unresolved_tag_names == ('TAG_B',)
    assert result.unresolved_count == 1
    assert 'TAG_B' not in registry.current()


def test_prepare_chunks_only_missing_webids_using_configured_points_limit(tmp_path) -> None:
    definitions = tuple(
        PiTagDefinition(
            tag_name=f'TAG_{index:03d}',
            alias=f'tag_{index:03d}',
            value_kind=PiValueKind.NUMBER,
            extraction_mode=PiExtractionMode.INTERPOLATED,
            materializations=(PiMaterialization.DAILY,),
        )
        for index in range(205)
    )
    from atlanticus.integrations.pi.contracts import PiWebApiSource

    catalog = PiCatalog(source=PiWebApiSource(interpolation_seconds=10), definitions=definitions)
    web_ids = {
        definition.tag_name: f'WEB_{index:03d}' for index, definition in enumerate(definitions)
    }
    client = FakeClient(web_ids, limit=100)
    registry = WebIdRegistry(path=tmp_path / 'webids.json')

    result = PiExecutionPlanPreparer(client=client, registry=registry).prepare(catalog)

    assert [len(call) for call in client.points.calls] == [100, 100, 5]
    assert result.point_request_count == 3
    assert result.resolved_count == 205
    assert len(result.plan.interpolated) == 205


def test_prepare_ignores_inactive_definitions(tmp_path, catalog) -> None:
    registry = WebIdRegistry(path=tmp_path / 'webids.json')
    client = FakeClient({'TAG_A': 'WEB_A', 'TAG_B': 'WEB_B', 'TAG_DISABLED': 'WEB_DISABLED'})

    result = PiExecutionPlanPreparer(client=client, registry=registry).prepare(catalog)

    assert client.points.calls == [('TAG_A', 'TAG_B')]
    assert 'TAG_DISABLED' not in registry.current()
    assert all(item.tag_name != 'TAG_DISABLED' for item in result.plan.resolved)


def test_prepare_rejects_non_web_api_catalog(tmp_path) -> None:
    catalog = PiCatalog(
        source=NotPiiSource(),
        definitions=(
            PiTagDefinition(
                tag_name='TAG_A',
                alias='a',
                value_kind=PiValueKind.NUMBER,
                extraction_mode=PiExtractionMode.INTERPOLATED,
                materializations=(PiMaterialization.DAILY,),
            ),
        ),
    )

    with pytest.raises(PiDataProducerCatalogError, match='source must be PiWebApiSource'):
        PiExecutionPlanPreparer(
            client=FakeClient({}),
            registry=WebIdRegistry(path=tmp_path / 'webids.json'),
        ).prepare(catalog)


def test_prepare_retries_point_resolution_timeout_three_times(
    tmp_path,
    catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        timeout_retry_module,
        '_TIMEOUT_RETRY_DELAYS_SECONDS',
        (0.0, 0.0, 0.0),
    )
    registry = WebIdRegistry(path=tmp_path / 'webids.json')
    client = FakeClient({})

    def timeout(tag_names):
        client.points.calls.append(tag_names)
        raise PiWebApiTimeoutError(phase='connect')

    monkeypatch.setattr(client.points, 'resolve_web_ids', timeout)

    context = _context(tmp_path)
    with pytest.raises(PiDataProducerTimeoutExhaustedError) as captured:
        PiExecutionPlanPreparer(client=client, registry=registry).prepare(
            catalog,
            context=context,
        )

    assert context.get_execution_fact('pi_timeout_retries') == 3
    assert captured.value.phase == 'connect'
    assert captured.value.retry_count == 3
    assert captured.value.point_request_count == 4
    assert len(client.points.calls) == 4
    assert dict(registry.current()) == {}


def test_prepare_preserves_successful_webid_chunks_before_later_timeout(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        timeout_retry_module,
        '_TIMEOUT_RETRY_DELAYS_SECONDS',
        (0.0, 0.0, 0.0),
    )
    definitions = tuple(
        PiTagDefinition(
            tag_name=f'TAG_{index}',
            alias=f'tag_{index}',
            value_kind=PiValueKind.NUMBER,
            extraction_mode=PiExtractionMode.INTERPOLATED,
            materializations=(PiMaterialization.DAILY,),
        )
        for index in range(3)
    )
    from atlanticus.integrations.pi.contracts import PiWebApiSource

    catalog = PiCatalog(source=PiWebApiSource(interpolation_seconds=10), definitions=definitions)
    client = FakeClient({'TAG_0': 'WEB_0', 'TAG_1': 'WEB_1'}, limit=2)
    registry = WebIdRegistry(path=tmp_path / 'webids.json')
    original = client.points.resolve_web_ids

    def resolve(tag_names):
        if tag_names == ('TAG_2',):
            client.points.calls.append(tag_names)
            raise PiWebApiTimeoutError(phase='connect')
        return original(tag_names)

    monkeypatch.setattr(client.points, 'resolve_web_ids', resolve)

    with pytest.raises(PiDataProducerTimeoutExhaustedError):
        PiExecutionPlanPreparer(client=client, registry=registry).prepare(
            catalog,
            context=_context(tmp_path),
        )

    assert dict(registry.current()) == {'TAG_0': 'WEB_0', 'TAG_1': 'WEB_1'}
    assert client.points.calls[0] == ('TAG_0', 'TAG_1')
    assert client.points.calls.count(('TAG_2',)) == 4
