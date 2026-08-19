from __future__ import annotations

from dataclasses import dataclass

from ada.kpis.core import (
    DataRuntimeContext,
    KpiArea,
    KpiCatalog,
    KpiMode,
    KpiSource,
    KpiSpec,
    KpiStatus,
    KpiValueKind,
    KpiWatermark,
    OverKpiSpec,
)
from ada.kpis.evaluation import KpiEvaluator
from tests.support import context


@dataclass
class FakeLoadedSources:
    contexts: dict[str, DataRuntimeContext]
    failures: set[str]

    def context_for(self, kpi_key: str) -> DataRuntimeContext:
        if kpi_key in self.failures:
            raise RuntimeError('source unavailable')
        return self.contexts.get(kpi_key, DataRuntimeContext(frames={}))


@dataclass
class FakeSourceLoader:
    loaded: FakeLoadedSources
    last_plan: object = None
    last_watermark: object = None

    def load(self, *, plan, watermark):
        self.last_plan = plan
        self.last_watermark = watermark
        return self.loaded


def _watermark() -> KpiWatermark:
    return KpiWatermark.parse('2026-08-19T18:15:20Z')


def test_evaluator_runs_entire_catalog_and_isolates_base_failures() -> None:
    source = KpiSource.PI_INTERPOLATED

    def explode(_):
        raise RuntimeError('boom')

    catalog = KpiCatalog(
        specs=(
            KpiSpec(
                key='good',
                area=KpiArea.MINA,
                mode=KpiMode.LATEST_NUMBER,
                source=source,
                columns=('value',),
                decimals=0,
            ),
            KpiSpec(
                key='resolver_error',
                area=KpiArea.MINA,
                mode=KpiMode.CUSTOM,
                source=source,
                columns=('value',),
                custom_resolver=explode,
            ),
            KpiSpec(
                key='source_error',
                area=KpiArea.MINA,
                mode=KpiMode.LATEST_NUMBER,
                source=source,
                columns=('value',),
                decimals=0,
            ),
        )
    )
    loader = FakeSourceLoader(
        FakeLoadedSources(
            contexts={
                'good': context(source, {'value': 8}),
                'resolver_error': context(source, {'value': 5}),
            },
            failures={'source_error'},
        )
    )
    evaluator = KpiEvaluator(source_loader=loader)

    evaluation = evaluator.evaluate(catalog=catalog, watermark=_watermark())
    results = {result.key: result for result in evaluation.results}

    assert results['good'].status is KpiStatus.OK
    assert results['good'].value == 8.0
    assert results['resolver_error'].status is KpiStatus.ERROR
    assert results['source_error'].status is KpiStatus.ERROR
    assert tuple(results) == ('good', 'resolver_error', 'source_error')


def test_evaluator_maps_status_contract_violation_to_invalid() -> None:
    source = KpiSource.PI_INTERPOLATED
    catalog = KpiCatalog(
        specs=(
            KpiSpec(
                key='status',
                area=KpiArea.PLANTA,
                mode=KpiMode.STATUS,
                source=source,
                columns=('status',),
                is_truncated=False,
            ),
        )
    )
    loader = FakeSourceLoader(
        FakeLoadedSources(
            contexts={'status': context(source, {'status': 'partiendo'})},
            failures=set(),
        )
    )

    evaluation = KpiEvaluator(source_loader=loader).evaluate(
        catalog=catalog,
        watermark=_watermark(),
    )

    assert evaluation.results[0].status is KpiStatus.INVALID


def test_over_kpi_runs_only_when_all_dependencies_are_ok() -> None:
    source = KpiSource.PI_INTERPOLATED
    catalog = KpiCatalog(
        specs=(
            KpiSpec(
                key='real',
                area=KpiArea.MINA,
                mode=KpiMode.LATEST_NUMBER,
                source=source,
                columns=('real',),
                decimals=0,
            ),
            KpiSpec(
                key='plan',
                area=KpiArea.MINA,
                mode=KpiMode.LATEST_NUMBER,
                source=source,
                columns=('plan',),
                decimals=0,
            ),
        ),
        over_specs=(
            OverKpiSpec(
                key='ratio',
                area=KpiArea.MINA,
                dependencies=('real', 'plan'),
                resolver=lambda values: values['real'] / values['plan'] * 100,
                decimals=1,
            ),
        ),
    )
    loader = FakeSourceLoader(
        FakeLoadedSources(
            contexts={
                'real': context(source, {'real': 8}),
                'plan': context(source, {'plan': 10}),
            },
            failures=set(),
        )
    )

    evaluation = KpiEvaluator(source_loader=loader).evaluate(
        catalog=catalog,
        watermark=_watermark(),
    )
    ratio = evaluation.results[-1]

    assert ratio.status is KpiStatus.OK
    assert ratio.value == 80.0
    assert ratio.parsed_value == '80,0'


def test_over_kpi_propagates_error_invalid_and_missing_without_running_resolver() -> None:
    source = KpiSource.PI_INTERPOLATED
    calls = []

    def resolver(values):
        calls.append(values)
        return 1

    catalog = KpiCatalog(
        specs=(
            KpiSpec(
                key='error',
                area=KpiArea.MINA,
                mode=KpiMode.LATEST_NUMBER,
                source=source,
                columns=('value',),
            ),
            KpiSpec(
                key='invalid',
                area=KpiArea.MINA,
                mode=KpiMode.CONSTANT,
                constant_value='bad',
            ),
            KpiSpec(
                key='missing',
                area=KpiArea.MINA,
                mode=KpiMode.CONSTANT,
                constant_value=None,
            ),
        ),
        over_specs=(
            OverKpiSpec(
                key='from_error',
                area=KpiArea.MINA,
                dependencies=('error',),
                resolver=resolver,
            ),
            OverKpiSpec(
                key='from_invalid',
                area=KpiArea.MINA,
                dependencies=('invalid',),
                resolver=resolver,
            ),
            OverKpiSpec(
                key='from_missing',
                area=KpiArea.MINA,
                dependencies=('missing',),
                resolver=resolver,
            ),
            OverKpiSpec(
                key='precedence',
                area=KpiArea.MINA,
                dependencies=('missing', 'invalid', 'error'),
                resolver=resolver,
            ),
        ),
    )
    loader = FakeSourceLoader(FakeLoadedSources(contexts={}, failures={'error'}))

    evaluation = KpiEvaluator(source_loader=loader).evaluate(
        catalog=catalog,
        watermark=_watermark(),
    )
    results = {result.key: result for result in evaluation.results}

    assert results['from_error'].status is KpiStatus.ERROR
    assert results['from_invalid'].status is KpiStatus.INVALID
    assert results['from_missing'].status is KpiStatus.MISSING
    assert results['precedence'].status is KpiStatus.ERROR
    assert calls == []


def test_over_kpi_gets_only_declared_native_values() -> None:
    captured = []

    def resolver(values):
        captured.append(values)
        return {'left': values['left']}

    catalog = KpiCatalog(
        specs=(
            KpiSpec(
                key='left',
                area=KpiArea.GENERAL,
                mode=KpiMode.CONSTANT,
                constant_value=2,
                decimals=0,
            ),
            KpiSpec(
                key='right',
                area=KpiArea.GENERAL,
                mode=KpiMode.CONSTANT,
                constant_value=3,
                decimals=0,
            ),
        ),
        over_specs=(
            OverKpiSpec(
                key='json',
                area=KpiArea.GENERAL,
                dependencies=('left',),
                resolver=resolver,
                value_kind=KpiValueKind.JSON,
                is_truncated=False,
            ),
        ),
    )
    loader = FakeSourceLoader(FakeLoadedSources(contexts={}, failures=set()))

    evaluation = KpiEvaluator(source_loader=loader).evaluate(
        catalog=catalog,
        watermark=_watermark(),
    )

    assert evaluation.results[-1].value == {'left': 2.0}
    assert tuple(captured[0]) == ('left',)


def test_evaluation_source_traces_are_top_level_and_only_for_requested_sources() -> None:
    source = KpiSource.PI_INTERPOLATED
    source_watermark = KpiWatermark.parse('2026-08-19T18:15:10Z')
    catalog = KpiCatalog(
        specs=(
            KpiSpec(
                key='value',
                area=KpiArea.MINA,
                mode=KpiMode.LATEST_NUMBER,
                source=source,
                columns=('value',),
            ),
        )
    )
    loader = FakeSourceLoader(
        FakeLoadedSources(
            contexts={'value': context(source, {'value': 1})},
            failures=set(),
        )
    )

    evaluation = KpiEvaluator(source_loader=loader).evaluate(
        catalog=catalog,
        watermark=_watermark(),
        source_watermarks={
            source: source_watermark,
            KpiSource.DISPATCH_TIEMPOS_MLP: None,
        },
    )

    assert len(evaluation.sources) == 1
    assert evaluation.sources[0].source is source
    assert evaluation.sources[0].watermark == source_watermark
    assert evaluation.as_document()['sources'] == {
        'pi.interpolated': {'watermark_utc': '2026-08-19T18:15:10Z'}
    }
