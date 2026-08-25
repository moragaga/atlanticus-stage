from __future__ import annotations

from collections.abc import Mapping

import pytest

from ada.data.core import (
    DataColumn,
    DataColumnType,
    DataPartition,
    DataRequirement,
    DataRuntimeContext,
    DataSource,
    ShiftScope,
    ShiftSelection,
    TimeWindow,
    TimeWindowUnit,
)
from ada.kpis.core import KpiArea, KpiMode, KpiSpec, KpiValueKind, OverKpiSpec
from ada.kpis.core.values import KpiNativeValue


def _column(name: str, data_type: DataColumnType = DataColumnType.FLOAT) -> DataColumn:
    return DataColumn(name=name, data_type=data_type)


def _resolver(data_context: DataRuntimeContext) -> KpiNativeValue:
    frame = data_context.get(DataSource.PI_INTERPOLATED, DataPartition.DAILY)
    return frame.last_value_number('tag_a')


def _over_resolver(values: Mapping[str, KpiNativeValue]) -> KpiNativeValue:
    return values['a']


def test_simple_spec_requires_explicit_source_partition() -> None:
    spec = KpiSpec(
        key='pi_value',
        area=KpiArea.GENERAL,
        mode=KpiMode.LATEST_NUMBER,
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(_column('tag_a'),),
        decimals=2,
        is_truncated=True,
        persist_history=True,
    )

    requirement = spec.requirements[0]
    assert requirement.source is DataSource.PI_INTERPOLATED
    assert requirement.partition is DataPartition.LATEST
    assert requirement.columns == (_column('tag_a'),)
    assert spec.column_names == ('tag_a',)
    assert spec.persist_history is True


def test_custom_spec_can_use_single_source_with_exact_partition_and_window() -> None:
    window = TimeWindow(2, TimeWindowUnit.HOURS)
    columns = (_column('tag_a'), _column('tag_b'), _column('tag_c'))
    spec = KpiSpec(
        key='pi_custom',
        area=KpiArea.PLANTA,
        mode=KpiMode.CUSTOM,
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.DAILY,
        columns=columns,
        time_window=window,
        custom_resolver=_resolver,
    )

    assert spec.requirements == (
        DataRequirement(
            source=DataSource.PI_INTERPOLATED,
            partition=DataPartition.DAILY,
            columns=columns,
            time_window=window,
        ),
    )


def test_custom_spec_supports_same_source_with_multiple_partitions_and_types() -> None:
    latest = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(_column('tag_latest'),),
    )
    daily = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.DAILY,
        columns=(_column('tag_series'),),
        time_window=TimeWindow(2, TimeWindowUnit.HOURS),
    )
    dispatch = DataRequirement(
        source=DataSource.DISPATCH_STD_SHIFT_STATE,
        partition=DataPartition.SHIFT,
        columns=(_column('state', DataColumnType.TEXT),),
        shift=ShiftSelection(ShiftScope.DAYS, days=3),
    )
    spec = KpiSpec(
        key='mixed',
        area=KpiArea.MINA,
        mode=KpiMode.CUSTOM,
        source_requirements=(latest, daily, dispatch),
        value_kind=KpiValueKind.JSON,
        custom_resolver=_resolver,
    )

    assert spec.requirements == (latest, daily, dispatch)


def test_custom_spec_rejects_duplicate_source_partition_views() -> None:
    with pytest.raises(ValueError, match='unique by source and partition'):
        KpiSpec(
            key='duplicate',
            area=KpiArea.MINA,
            mode=KpiMode.CUSTOM,
            source_requirements=(
                DataRequirement(
                    source=DataSource.PI_INTERPOLATED,
                    partition=DataPartition.LATEST,
                    columns=(_column('a'),),
                ),
                DataRequirement(
                    source=DataSource.PI_INTERPOLATED,
                    partition=DataPartition.LATEST,
                    columns=(_column('b'),),
                ),
            ),
            custom_resolver=_resolver,
        )


def test_spec_requires_shared_data_contracts_instead_of_free_strings() -> None:
    with pytest.raises(TypeError, match='area must be KpiArea'):
        KpiSpec(
            key='invalid',
            area='mina',  # type: ignore[arg-type]
            mode=KpiMode.LATEST,
            source=DataSource.PI_INTERPOLATED,
            partition=DataPartition.LATEST,
            columns=(_column('tag_a'),),
        )

    with pytest.raises(TypeError, match='source must be DataSource'):
        KpiSpec(
            key='invalid',
            area=KpiArea.MINA,
            mode=KpiMode.LATEST,
            source='pi.interpolated',  # type: ignore[arg-type]
            partition=DataPartition.LATEST,
            columns=(_column('tag_a'),),
        )

    with pytest.raises(TypeError, match='columns must contain DataColumn'):
        KpiSpec(
            key='invalid',
            area=KpiArea.MINA,
            mode=KpiMode.LATEST,
            source=DataSource.PI_INTERPOLATED,
            partition=DataPartition.LATEST,
            columns=('tag_a',),  # type: ignore[arg-type]
        )


def test_simple_modes_keep_column_count_and_type_rules() -> None:
    with pytest.raises(ValueError, match='requires exactly one column'):
        KpiSpec(
            key='latest',
            area=KpiArea.MINA,
            mode=KpiMode.LATEST,
            source=DataSource.PI_INTERPOLATED,
            partition=DataPartition.LATEST,
            columns=(_column('a'), _column('b')),
        )

    spec = KpiSpec(
        key='sum',
        area=KpiArea.MINA,
        mode=KpiMode.SUM_LATESTS_NUMBERS,
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(_column('a', DataColumnType.INTEGER), _column('b')),
    )
    assert spec.mode is KpiMode.SUM_LATESTS_NUMBERS

    with pytest.raises(ValueError, match='does not support column types'):
        KpiSpec(
            key='sum-text',
            area=KpiArea.MINA,
            mode=KpiMode.SUM_LATESTS_NUMBERS,
            source=DataSource.PI_INTERPOLATED,
            partition=DataPartition.LATEST,
            columns=(_column('a', DataColumnType.TEXT),),
        )

    KpiSpec(
        key='status-text',
        area=KpiArea.MINA,
        mode=KpiMode.STATUS,
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(_column('state', DataColumnType.TEXT),),
    )


def test_constant_and_custom_contracts_are_mutually_exclusive() -> None:
    constant = KpiSpec(
        key='constant',
        area=KpiArea.GENERAL,
        mode=KpiMode.CONSTANT,
        constant_value=42,
    )
    assert constant.requirements == ()

    with pytest.raises(ValueError, match='cannot mix source and source_requirements'):
        KpiSpec(
            key='bad-custom',
            area=KpiArea.MINA,
            mode=KpiMode.CUSTOM,
            source=DataSource.PI_INTERPOLATED,
            partition=DataPartition.LATEST,
            columns=(_column('a'),),
            source_requirements=(
                DataRequirement(
                    source=DataSource.REMANENTES_STOCKS,
                    partition=DataPartition.LATEST,
                    columns=(_column('b'),),
                ),
            ),
            custom_resolver=_resolver,
        )


def test_over_kpi_spec_keeps_output_contract() -> None:
    spec = OverKpiSpec(
        key='over',
        area=KpiArea.PLANTA,
        dependencies=('a', 'b'),
        resolver=_over_resolver,
        decimals=1,
        is_truncated=False,
        value_kind=KpiValueKind.VALUE,
        persist_history=True,
    )

    assert spec.dependencies == ('a', 'b')
    assert spec.decimals == 1
    assert spec.is_truncated is False
    assert spec.persist_history is True
