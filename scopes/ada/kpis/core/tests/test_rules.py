from __future__ import annotations

from collections.abc import Mapping

import pytest

from ada.kpis.core import (
    DataRuntimeContext,
    KpiArea,
    KpiMode,
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
    frame = data_context.get(KpiSource.PI_INTERPOLATED)
    return frame.last_value_number('tag_a')


def _over_resolver(values: Mapping[str, KpiNativeValue]) -> KpiNativeValue:
    return values['a']


def test_spec_preserves_legacy_shape_without_unit() -> None:
    spec = KpiSpec(
        key='pi_value',
        area=KpiArea.GENERAL,
        mode=KpiMode.LATEST_NUMBER,
        source=KpiSource.PI_INTERPOLATED,
        columns=('tag_a',),
        decimals=2,
        is_truncated=True,
        persist_history=True,
    )

    assert spec.area is KpiArea.GENERAL
    assert spec.decimals == 2
    assert spec.is_truncated is True
    assert spec.persist_history is True
    assert not hasattr(spec, 'unit')


def test_custom_spec_can_use_single_source_with_exact_window() -> None:
    spec = KpiSpec(
        key='pi_custom',
        area=KpiArea.PLANTA,
        mode=KpiMode.CUSTOM,
        source=KpiSource.PI_INTERPOLATED,
        columns=('tag_a', 'tag_b', 'tag_c'),
        time_window=KpiTimeWindow(2, KpiTimeWindowUnit.HOURS),
        custom_resolver=_resolver,
    )

    requirement = spec.requirements[KpiSource.PI_INTERPOLATED]
    assert requirement.columns == ('tag_a', 'tag_b', 'tag_c')
    assert requirement.time_window == KpiTimeWindow(2, KpiTimeWindowUnit.HOURS)


def test_custom_spec_preserves_requirements_by_source_semantics() -> None:
    spec = KpiSpec(
        key='mixed',
        area=KpiArea.MINA,
        mode=KpiMode.CUSTOM,
        requirements_by_source={
            KpiSource.PI_INTERPOLATED: SourceRequirement(
                columns=('tag_a', 'tag_b', 'tag_c'),
                time_window=KpiTimeWindow(2, KpiTimeWindowUnit.HOURS),
            ),
            KpiSource.DISPATCH_STD_SHIFT_STATE: SourceRequirement(
                columns=('shift_id', 'state', 'truck'),
                shift=ShiftSelection(ShiftScope.DAYS, days=3),
            ),
        },
        value_kind=KpiValueKind.JSON,
        custom_resolver=_resolver,
    )

    assert tuple(spec.requirements) == (
        KpiSource.PI_INTERPOLATED,
        KpiSource.DISPATCH_STD_SHIFT_STATE,
    )
    assert spec.requirements[KpiSource.DISPATCH_STD_SHIFT_STATE].shift == ShiftSelection(
        ShiftScope.DAYS,
        days=3,
    )


def test_spec_requires_enums_instead_of_free_strings() -> None:
    with pytest.raises(TypeError, match='area must be KpiArea'):
        KpiSpec(
            key='invalid',
            area='mina',  # type: ignore[arg-type]
            mode=KpiMode.LATEST,
            source=KpiSource.PI_INTERPOLATED,
            columns=('tag_a',),
        )

    with pytest.raises(TypeError, match='source must be KpiSource'):
        KpiSpec(
            key='invalid',
            area=KpiArea.MINA,
            mode=KpiMode.LATEST,
            source='pi.interpolated',  # type: ignore[arg-type]
            columns=('tag_a',),
        )


def test_simple_modes_keep_legacy_column_rules() -> None:
    with pytest.raises(ValueError, match='requires exactly one column'):
        KpiSpec(
            key='latest',
            area=KpiArea.MINA,
            mode=KpiMode.LATEST,
            source=KpiSource.PI_INTERPOLATED,
            columns=('a', 'b'),
        )

    spec = KpiSpec(
        key='sum',
        area=KpiArea.MINA,
        mode=KpiMode.SUM_LATESTS_NUMBERS,
        source=KpiSource.PI_INTERPOLATED,
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
    assert constant.requirements == {}

    with pytest.raises(ValueError, match='cannot mix source and requirements_by_source'):
        KpiSpec(
            key='bad-custom',
            area=KpiArea.MINA,
            mode=KpiMode.CUSTOM,
            source=KpiSource.PI_INTERPOLATED,
            columns=('a',),
            requirements_by_source={
                KpiSource.DISPATCH_TIEMPOS_MLP: SourceRequirement(columns=('b',))
            },
            custom_resolver=_resolver,
        )


def test_over_kpi_spec_keeps_legacy_output_contract() -> None:
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
    assert not hasattr(spec, 'unit')
