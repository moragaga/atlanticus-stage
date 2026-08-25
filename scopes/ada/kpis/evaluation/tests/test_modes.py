import pytest

from ada.data.core import (
    DataColumn,
    DataColumnType,
    DataPartition,
    DataSource,
    TimeWindow,
    TimeWindowUnit,
)
from ada.kpis.core import KpiArea, KpiMode, KpiSpec
from ada.kpis.evaluation.errors import KpiInvalidValueError
from ada.kpis.evaluation.modes import resolve_base_value
from tests.support import context


def _column(name: str, data_type: DataColumnType = DataColumnType.FLOAT) -> DataColumn:
    return DataColumn(name=name, data_type=data_type)


def test_latest_and_latest_number_use_source_partition_context_helpers() -> None:
    source = DataSource.PI_INTERPOLATED
    data_context = context(source, {'raw': 'abc', 'number': '12.5'})

    latest = KpiSpec(
        key='latest',
        area=KpiArea.MINA,
        mode=KpiMode.LATEST,
        source=source,
        partition=DataPartition.LATEST,
        columns=(_column('raw', DataColumnType.TEXT),),
        is_truncated=False,
    )
    latest_number = KpiSpec(
        key='latest_number',
        area=KpiArea.MINA,
        mode=KpiMode.LATEST_NUMBER,
        source=source,
        partition=DataPartition.LATEST,
        columns=(_column('number'),),
    )

    assert resolve_base_value(spec=latest, data_context=data_context) == 'abc'
    assert resolve_base_value(spec=latest_number, data_context=data_context) == 12.5


def test_status_has_small_explicit_contract() -> None:
    source = DataSource.PI_INTERPOLATED
    spec = KpiSpec(
        key='status',
        area=KpiArea.PLANTA,
        mode=KpiMode.STATUS,
        source=source,
        partition=DataPartition.LATEST,
        columns=(_column('status', DataColumnType.TEXT),),
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
    source = DataSource.PI_INTERPOLATED
    spec = KpiSpec(
        key='status',
        area=KpiArea.PLANTA,
        mode=KpiMode.STATUS,
        source=source,
        partition=DataPartition.LATEST,
        columns=(_column('status', DataColumnType.TEXT),),
        is_truncated=False,
    )

    assert resolve_base_value(spec=spec, data_context=context(source, {'status': None})) is None


def test_sum_treats_missing_latest_numbers_as_zero() -> None:
    source = DataSource.PI_INTERPOLATED
    spec = KpiSpec(
        key='sum',
        area=KpiArea.MINA,
        mode=KpiMode.SUM_LATESTS_NUMBERS,
        source=source,
        partition=DataPartition.LATEST,
        columns=(_column('a'), _column('b'), _column('c')),
    )

    value = resolve_base_value(
        spec=spec,
        data_context=context(source, {'a': 4, 'b': None, 'c': 'bad'}),
    )

    assert value == 4.0


def test_max_ignores_missing_values_and_returns_none_when_all_are_missing() -> None:
    source = DataSource.PI_INTERPOLATED
    spec = KpiSpec(
        key='max',
        area=KpiArea.MINA,
        mode=KpiMode.MAX_LATESTS_NUMBERS,
        source=source,
        partition=DataPartition.LATEST,
        columns=(_column('a'), _column('b')),
    )

    assert resolve_base_value(spec=spec, data_context=context(source, {'a': 2, 'b': 5})) == 5.0
    assert (
        resolve_base_value(spec=spec, data_context=context(source, {'a': None, 'b': 'bad'})) is None
    )


def test_custom_receives_exact_data_runtime_context_for_requested_partition() -> None:
    source = DataSource.PI_RECORDED
    partition = DataPartition.DAILY
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
        columns=(_column('value'),),
        time_window=TimeWindow(1, TimeWindowUnit.HOURS),
        custom_resolver=resolver,
    )
    data_context = context(source, {'value': 7}, partition=partition)

    assert resolve_base_value(spec=spec, data_context=data_context) == 7.0
    assert captured == [data_context]
