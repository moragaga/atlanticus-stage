import pytest

from ada.kpis.core import KpiArea, KpiMode, KpiSource, KpiSpec
from ada.kpis.evaluation.errors import KpiInvalidValueError
from ada.kpis.evaluation.modes import resolve_base_value
from tests.support import context


def test_latest_and_latest_number_use_source_context_helpers() -> None:
    data_context = context(KpiSource.PI_INTERPOLATED, {'raw': 'abc', 'number': '12.5'})

    latest = KpiSpec(
        key='latest',
        area=KpiArea.MINA,
        mode=KpiMode.LATEST,
        source=KpiSource.PI_INTERPOLATED,
        columns=('raw',),
        is_truncated=False,
    )
    latest_number = KpiSpec(
        key='latest_number',
        area=KpiArea.MINA,
        mode=KpiMode.LATEST_NUMBER,
        source=KpiSource.PI_INTERPOLATED,
        columns=('number',),
    )

    assert resolve_base_value(spec=latest, data_context=data_context) == 'abc'
    assert resolve_base_value(spec=latest_number, data_context=data_context) == 12.5


def test_status_has_small_explicit_contract() -> None:
    source = KpiSource.PI_INTERPOLATED
    spec = KpiSpec(
        key='status',
        area=KpiArea.PLANTA,
        mode=KpiMode.STATUS,
        source=source,
        columns=('status',),
        is_truncated=False,
    )

    assert resolve_base_value(spec=spec, data_context=context(source, {'status': 0})) == 'detenido'
    assert resolve_base_value(spec=spec, data_context=context(source, {'status': 1})) == 'operando'
    assert (
        resolve_base_value(spec=spec, data_context=context(source, {'status': 'detenido'}))
        == 'detenido'
    )
    assert (
        resolve_base_value(spec=spec, data_context=context(source, {'status': 'funcionando'}))
        == 'operando'
    )

    with pytest.raises(KpiInvalidValueError):
        resolve_base_value(spec=spec, data_context=context(source, {'status': 'partiendo'}))


def test_status_missing_remains_missing_value() -> None:
    source = KpiSource.PI_INTERPOLATED
    spec = KpiSpec(
        key='status',
        area=KpiArea.PLANTA,
        mode=KpiMode.STATUS,
        source=source,
        columns=('status',),
        is_truncated=False,
    )

    assert resolve_base_value(spec=spec, data_context=context(source, {'status': None})) is None


def test_sum_treats_missing_latest_numbers_as_zero() -> None:
    source = KpiSource.PI_INTERPOLATED
    spec = KpiSpec(
        key='sum',
        area=KpiArea.MINA,
        mode=KpiMode.SUM_LATESTS_NUMBERS,
        source=source,
        columns=('a', 'b', 'c'),
    )

    value = resolve_base_value(
        spec=spec,
        data_context=context(source, {'a': 4, 'b': None, 'c': 'bad'}),
    )

    assert value == 4.0


def test_max_ignores_missing_values_and_returns_none_when_all_are_missing() -> None:
    source = KpiSource.PI_INTERPOLATED
    spec = KpiSpec(
        key='max',
        area=KpiArea.MINA,
        mode=KpiMode.MAX_LATESTS_NUMBERS,
        source=source,
        columns=('a', 'b'),
    )

    assert resolve_base_value(spec=spec, data_context=context(source, {'a': 2, 'b': 5})) == 5.0
    assert (
        resolve_base_value(spec=spec, data_context=context(source, {'a': None, 'b': 'bad'})) is None
    )


def test_custom_receives_exact_data_runtime_context() -> None:
    source = KpiSource.PI_RECORDED
    captured = []

    def resolver(data_context):
        captured.append(data_context)
        return data_context.get(source).last_value_number('value')

    spec = KpiSpec(
        key='custom',
        area=KpiArea.GENERAL,
        mode=KpiMode.CUSTOM,
        source=source,
        columns=('value',),
        custom_resolver=resolver,
    )
    data_context = context(source, {'value': 7})

    assert resolve_base_value(spec=spec, data_context=data_context) == 7.0
    assert captured == [data_context]
