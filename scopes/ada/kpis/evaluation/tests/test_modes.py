import pytest

from ada.kpis.core import (
    KpiArea,
    KpiMode,
    KpiPartition,
    KpiSource,
    KpiSpec,
    KpiTimeWindow,
    KpiTimeWindowUnit,
)
from ada.kpis.evaluation.errors import KpiInvalidValueError
from ada.kpis.evaluation.modes import resolve_base_value
from tests.support import context


def test_latest_and_latest_number_use_source_partition_context_helpers() -> None:
    source = KpiSource.PI_INTERPOLATED
    data_context = context(source, {'raw': 'abc', 'number': '12.5'})

    latest = KpiSpec(
        key='latest',
        area=KpiArea.MINA,
        mode=KpiMode.LATEST,
        source=source,
        partition=KpiPartition.LATEST,
        columns=('raw',),
        is_truncated=False,
    )
    latest_number = KpiSpec(
        key='latest_number',
        area=KpiArea.MINA,
        mode=KpiMode.LATEST_NUMBER,
        source=source,
        partition=KpiPartition.LATEST,
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
        partition=KpiPartition.LATEST,
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


def test_status_without_value_returns_none_for_evaluator_to_mark_error() -> None:
    source = KpiSource.PI_INTERPOLATED
    spec = KpiSpec(
        key='status',
        area=KpiArea.PLANTA,
        mode=KpiMode.STATUS,
        source=source,
        partition=KpiPartition.LATEST,
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
        partition=KpiPartition.LATEST,
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
        partition=KpiPartition.LATEST,
        columns=('a', 'b'),
    )

    assert resolve_base_value(spec=spec, data_context=context(source, {'a': 2, 'b': 5})) == 5.0
    assert (
        resolve_base_value(spec=spec, data_context=context(source, {'a': None, 'b': 'bad'})) is None
    )


def test_custom_receives_exact_data_runtime_context_for_requested_partition() -> None:
    source = KpiSource.PI_RECORDED
    partition = KpiPartition.DAILY
    captured = []

    def resolver(data_context):
        captured.append(data_context)
        return data_context.get(source, partition).last_value_number('value')

    spec = KpiSpec(
        key='custom',
        area=KpiArea.GENERAL,
        mode=KpiMode.CUSTOM,
        source=source,
        partition=partition,
        columns=('value',),
        time_window=KpiTimeWindow(1, KpiTimeWindowUnit.HOURS),
        custom_resolver=resolver,
    )
    data_context = context(source, {'value': 7}, partition=partition)

    assert resolve_base_value(spec=spec, data_context=data_context) == 7.0
    assert captured == [data_context]
