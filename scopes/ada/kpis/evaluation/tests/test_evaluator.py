from __future__ import annotations

from dataclasses import dataclass

from ada.kpis.core import (
    DataRuntimeContext,
    KpiArea,
    KpiCatalog,
    KpiMode,
    KpiPartition,
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


def _latest_spec(key: str, *, column: str = 'value', decimals: int | None = 0) -> KpiSpec:
    return KpiSpec(
        key=key,
        area=KpiArea.MINA,
        mode=KpiMode.LATEST_NUMBER,
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.LATEST,
        columns=(column,),
        decimals=decimals,
    )


def test_evaluator_runs_entire_catalog_and_isolates_base_failures() -> None:
    source = KpiSource.PI_INTERPOLATED

    def explode(_):
        raise RuntimeError('boom')

    catalog = KpiCatalog(
        specs=(
            _latest_spec('good'),
            KpiSpec(
                key='resolver_error',
                area=KpiArea.MINA,
                mode=KpiMode.CUSTOM,
                source=source,
                partition=KpiPartition.LATEST,
                columns=('value',),
                custom_resolver=explode,
            ),
            _latest_spec('source_error'),
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

    evaluation = KpiEvaluator(source_loader=loader).evaluate(
        catalog=catalog,
        watermark=_watermark(),
    )
    results = {result.key: result for result in evaluation.results}

    assert results['good'].status is KpiStatus.OK
    assert results['good'].value == 8.0
    assert results['resolver_error'].status is KpiStatus.ERROR
    assert results['resolver_error'].error == 'RuntimeError'
    assert results['source_error'].status is KpiStatus.ERROR
    assert results['source_error'].error == 'RuntimeError'
    assert tuple(results) == ('good', 'resolver_error', 'source_error')


def test_status_contract_violation_is_evaluation_error() -> None:
    source = KpiSource.PI_INTERPOLATED
    catalog = KpiCatalog(
        specs=(
            KpiSpec(
                key='status',
                area=KpiArea.PLANTA,
                mode=KpiMode.STATUS,
                source=source,
                partition=KpiPartition.LATEST,
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

    result = (
        KpiEvaluator(source_loader=loader)
        .evaluate(
            catalog=catalog,
            watermark=_watermark(),
        )
        .results[0]
    )

    assert result.status is KpiStatus.ERROR
    assert result.error == 'KpiInvalidValueError: status value is invalid'


def test_over_kpi_runs_only_when_all_dependencies_are_ok() -> None:
    source = KpiSource.PI_INTERPOLATED
    catalog = KpiCatalog(
        specs=(
            _latest_spec('real', column='real'),
            _latest_spec('plan', column='plan'),
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

    ratio = (
        KpiEvaluator(source_loader=loader)
        .evaluate(
            catalog=catalog,
            watermark=_watermark(),
        )
        .results[-1]
    )

    assert ratio.status is KpiStatus.OK
    assert ratio.value == 80.0
    assert ratio.parsed_value == '80,0'


def test_over_kpi_propagates_any_dependency_error_without_running_resolver() -> None:
    calls = []

    def resolver(values):
        calls.append(values)
        return 1

    catalog = KpiCatalog(
        specs=(
            _latest_spec('source_error'),
            KpiSpec(
                key='invalid_constant',
                area=KpiArea.MINA,
                mode=KpiMode.CONSTANT,
                constant_value='bad',
            ),
            KpiSpec(
                key='empty_constant',
                area=KpiArea.MINA,
                mode=KpiMode.CONSTANT,
                constant_value=None,
            ),
        ),
        over_specs=(
            OverKpiSpec(
                key='derived',
                area=KpiArea.MINA,
                dependencies=('source_error', 'invalid_constant', 'empty_constant'),
                resolver=resolver,
            ),
        ),
    )
    loader = FakeSourceLoader(FakeLoadedSources(contexts={}, failures={'source_error'}))

    results = {
        result.key: result
        for result in KpiEvaluator(source_loader=loader)
        .evaluate(
            catalog=catalog,
            watermark=_watermark(),
        )
        .results
    }

    assert results['source_error'].status is KpiStatus.ERROR
    assert results['invalid_constant'].status is KpiStatus.ERROR
    assert results['empty_constant'].status is KpiStatus.ERROR
    assert results['derived'].status is KpiStatus.ERROR
    assert results['derived'].error == (
        'KPI dependency failed: source_error, invalid_constant, empty_constant'
    )
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


def test_evaluation_source_traces_are_deduplicated_across_partitions() -> None:
    source = KpiSource.PI_INTERPOLATED
    source_watermark = KpiWatermark.parse('2026-08-19T18:15:10Z')
    catalog = KpiCatalog(specs=(_latest_spec('value'),))
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


def test_custom_resolver_contract_error_keeps_safe_context_in_evaluation() -> None:
    source = KpiSource.PI_INTERPOLATED

    def resolver(data_context: DataRuntimeContext):
        return data_context.get(KpiSource.PI_RECORDED, KpiPartition.DAILY).last_value('value')

    catalog = KpiCatalog(
        specs=(
            KpiSpec(
                key='bad-contract',
                area=KpiArea.MINA,
                mode=KpiMode.CUSTOM,
                source=source,
                partition=KpiPartition.LATEST,
                columns=('value',),
                custom_resolver=resolver,
            ),
        )
    )
    loader = FakeSourceLoader(
        FakeLoadedSources(
            contexts={'bad-contract': context(source, {'value': 5})},
            failures=set(),
        )
    )

    result = (
        KpiEvaluator(source_loader=loader)
        .evaluate(
            catalog=catalog,
            watermark=_watermark(),
        )
        .results[0]
    )

    assert result.status is KpiStatus.ERROR
    assert result.error is not None
    assert result.error.startswith('KpiSourceNotRequestedError:')
    assert 'pi.recorded/daily' in result.error
