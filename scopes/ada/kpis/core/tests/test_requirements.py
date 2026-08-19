from __future__ import annotations

from datetime import timedelta

import pytest

from ada.kpis.core import (
    KpiTimeWindow,
    KpiTimeWindowUnit,
    ShiftScope,
    ShiftSelection,
    SourceRequirement,
)


def test_time_window_keeps_delta_semantics() -> None:
    assert KpiTimeWindow(30, KpiTimeWindowUnit.MINUTES).to_timedelta() == timedelta(minutes=30)
    assert KpiTimeWindow(2, KpiTimeWindowUnit.HOURS).to_timedelta() == timedelta(hours=2)
    assert KpiTimeWindow(3, KpiTimeWindowUnit.DAYS).to_timedelta() == timedelta(days=3)


def test_time_window_requires_typed_unit() -> None:
    with pytest.raises(TypeError, match='KpiTimeWindowUnit'):
        KpiTimeWindow(2, 'hours')  # type: ignore[arg-type]


def test_shift_scopes_preserve_operational_contract() -> None:
    assert ShiftSelection(ShiftScope.CURRENT).days is None
    assert ShiftSelection(ShiftScope.PREVIOUS).days is None
    assert ShiftSelection(ShiftScope.CURRENT_TURN).days is None
    assert ShiftSelection(ShiftScope.PREVIOUS_TURN).days is None
    assert ShiftSelection(ShiftScope.CURRENT_WEEK).days is None


def test_days_shift_scope_is_parameterized_from_one_to_seven() -> None:
    assert ShiftSelection(ShiftScope.DAYS, days=1).days == 1
    assert ShiftSelection(ShiftScope.DAYS, days=7).days == 7

    with pytest.raises(ValueError, match='requires an integer days value'):
        ShiftSelection(ShiftScope.DAYS)
    with pytest.raises(ValueError, match='between 1 and 7'):
        ShiftSelection(ShiftScope.DAYS, days=8)
    with pytest.raises(ValueError, match='only be declared'):
        ShiftSelection(ShiftScope.CURRENT, days=1)


def test_source_requirement_is_exact_and_cannot_mix_time_and_shift() -> None:
    requirement = SourceRequirement(
        columns=('tag_a', 'tag_b'),
        time_window=KpiTimeWindow(2, KpiTimeWindowUnit.HOURS),
    )
    assert requirement.columns == ('tag_a', 'tag_b')

    with pytest.raises(ValueError, match='cannot mix'):
        SourceRequirement(
            columns=('tag_a',),
            time_window=KpiTimeWindow(2, KpiTimeWindowUnit.HOURS),
            shift=ShiftSelection(ShiftScope.CURRENT),
        )


def test_source_requirement_rejects_empty_or_duplicate_columns() -> None:
    with pytest.raises(ValueError, match='at least one column'):
        SourceRequirement(columns=())
    with pytest.raises(ValueError, match='unique'):
        SourceRequirement(columns=('a', 'a'))
