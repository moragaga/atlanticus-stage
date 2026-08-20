from __future__ import annotations

from collections.abc import Mapping

import pytest

from ada.kpis.core import (
    DataRuntimeContext,
    KpiArea,
    KpiMode,
    KpiPartition,
    KpiSource,
    KpiSpec,
    KpiTimeWindow,
    KpiTimeWindowUnit,
    KpiValueKind,
    OverKpiSpec,
    ShiftScope,
    ShiftSelection,
    SourceRequirement,
)
from ada.kpis.core.values import KpiNativeValue


def _resolver(data_context: DataRuntimeContext) -> KpiNativeValue:
    frame = data_context.get(KpiSource.PI_INTERPOLATED, KpiPartition.DAILY)
    return frame.last_value_number('tag_a')


def _over_resolver(values: Mapping[str, KpiNativeValue]) -> KpiNativeValue:
    return values['a']


def test_simple_spec_requires_explicit_source_partition() -> None:
    spec = KpiSpec(
        key='pi_value',
        area=KpiArea.GENERAL,
        mode=KpiMode.LATEST_NUMBER,
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.LATEST,
        columns=('tag_a',),
        decimals=2,
        is_truncated=True,
        persist_history=True,
    )

    requirement = spec.requirements[0]
    assert requirement.source is KpiSource.PI_INTERPOLATED
    assert requirement.partition is KpiPartition.LATEST
    assert spec.persist_history is True


def test_custom_spec_can_use_single_source_with_exact_partition_and_window() -> None:
    window = KpiTimeWindow(2, KpiTimeWindowUnit.HOURS)
    spec = KpiSpec(
        key='pi_custom',
        area=KpiArea.PLANTA,
        mode=KpiMode.CUSTOM,
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.DAILY,
        columns=('tag_a', 'tag_b', 'tag_c'),
        time_window=window,
        custom_resolver=_resolver,
    )

    assert spec.requirements == (
        SourceRequirement(
            source=KpiSource.PI_INTERPOLATED,
            partition=KpiPartition.DAILY,
            columns=('tag_a', 'tag_b', 'tag_c'),
            time_window=window,
        ),
    )


def test_custom_spec_supports_same_source_with_multiple_partitions() -> None:
    latest = SourceRequirement(
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.LATEST,
        columns=('tag_latest',),
    )
    daily = SourceRequirement(
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.DAILY,
        columns=('tag_series',),
        time_window=KpiTimeWindow(2, KpiTimeWindowUnit.HOURS),
    )
    dispatch = SourceRequirement(
        source=KpiSource.DISPATCH_STD_SHIFT_STATE,
        partition=KpiPartition.SHIFT,
        columns=('state',),
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
                SourceRequirement(
                    source=KpiSource.PI_INTERPOLATED,
                    partition=KpiPartition.LATEST,
                    columns=('a',),
                ),
                SourceRequirement(
                    source=KpiSource.PI_INTERPOLATED,
                    partition=KpiPartition.LATEST,
                    columns=('b',),
                ),
            ),
            custom_resolver=_resolver,
        )


def test_spec_requires_enums_instead_of_free_strings() -> None:
    with pytest.raises(TypeError, match='area must be KpiArea'):
        KpiSpec(
            key='invalid',
            area='mina',  # type: ignore[arg-type]
            mode=KpiMode.LATEST,
            source=KpiSource.PI_INTERPOLATED,
            partition=KpiPartition.LATEST,
            columns=('tag_a',),
        )

    with pytest.raises(TypeError, match='source must be KpiSource'):
        KpiSpec(
            key='invalid',
            area=KpiArea.MINA,
            mode=KpiMode.LATEST,
            source='pi.interpolated',  # type: ignore[arg-type]
            partition=KpiPartition.LATEST,
            columns=('tag_a',),
        )


def test_simple_modes_keep_column_rules() -> None:
    with pytest.raises(ValueError, match='requires exactly one column'):
        KpiSpec(
            key='latest',
            area=KpiArea.MINA,
            mode=KpiMode.LATEST,
            source=KpiSource.PI_INTERPOLATED,
            partition=KpiPartition.LATEST,
            columns=('a', 'b'),
        )

    spec = KpiSpec(
        key='sum',
        area=KpiArea.MINA,
        mode=KpiMode.SUM_LATESTS_NUMBERS,
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.LATEST,
        columns=('a', 'b'),
    )
    assert spec.mode is KpiMode.SUM_LATESTS_NUMBERS


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
            source=KpiSource.PI_INTERPOLATED,
            partition=KpiPartition.LATEST,
            columns=('a',),
            source_requirements=(
                SourceRequirement(
                    source=KpiSource.REMANENTES_STOCKS,
                    partition=KpiPartition.LATEST,
                    columns=('b',),
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
