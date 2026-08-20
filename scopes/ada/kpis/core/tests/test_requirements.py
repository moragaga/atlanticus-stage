from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ada.kpis.core import (
    KpiOperationalScope,
    KpiPartition,
    KpiSource,
    KpiSourceView,
    KpiTimeWindow,
    KpiTimeWindowUnit,
    ShiftScope,
    ShiftSelection,
    SourceRequirement,
)


def test_time_window_keeps_fixed_delta_semantics_and_calendar_months() -> None:
    assert KpiTimeWindow(30, KpiTimeWindowUnit.MINUTES).to_timedelta() == timedelta(minutes=30)
    assert KpiTimeWindow(2, KpiTimeWindowUnit.HOURS).to_timedelta() == timedelta(hours=2)
    assert KpiTimeWindow(3, KpiTimeWindowUnit.DAYS).to_timedelta() == timedelta(days=3)

    monthly = KpiTimeWindow(1, KpiTimeWindowUnit.MONTHS)
    with pytest.raises(ValueError, match='no fixed timedelta'):
        monthly.to_timedelta()
    assert monthly.start_from(datetime(2026, 3, 31, 12, tzinfo=UTC)) == datetime(
        2026, 2, 28, 12, tzinfo=UTC
    )


def test_time_window_requires_typed_unit() -> None:
    with pytest.raises(TypeError, match='KpiTimeWindowUnit'):
        KpiTimeWindow(2, 'hours')  # type: ignore[arg-type]


def test_shift_scopes_preserve_dispatch_contract() -> None:
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


def test_source_requirement_has_typed_source_partition_and_exact_view_identity() -> None:
    requirement = SourceRequirement(
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.DAILY,
        columns=('tag_a', 'tag_b'),
        time_window=KpiTimeWindow(2, KpiTimeWindowUnit.HOURS),
    )

    assert requirement.columns == ('tag_a', 'tag_b')
    assert requirement.view == KpiSourceView(
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.DAILY,
    )


def test_pi_partition_contract_rejects_invalid_combinations() -> None:
    SourceRequirement(
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.LATEST,
        columns=('tag_a',),
    )
    SourceRequirement(
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.DAILY,
        columns=('tag_a',),
        operational_scope=KpiOperationalScope.CURRENT_TURN_PLANT,
    )
    SourceRequirement(
        source=KpiSource.PI_RECORDED,
        partition=KpiPartition.MONTHLY,
        columns=('tag_a',),
        time_window=KpiTimeWindow(2, KpiTimeWindowUnit.MONTHS),
    )

    with pytest.raises(ValueError, match='latest partition must not declare'):
        SourceRequirement(
            source=KpiSource.PI_INTERPOLATED,
            partition=KpiPartition.LATEST,
            columns=('tag_a',),
            time_window=KpiTimeWindow(2, KpiTimeWindowUnit.HOURS),
        )
    with pytest.raises(ValueError, match='unsupported partition'):
        SourceRequirement(
            source=KpiSource.PI_RECORDED,
            partition=KpiPartition.LATEST,
            columns=('tag_a',),
        )
    with pytest.raises(ValueError, match='months time window requires monthly'):
        SourceRequirement(
            source=KpiSource.PI_INTERPOLATED,
            partition=KpiPartition.DAILY,
            columns=('tag_a',),
            time_window=KpiTimeWindow(1, KpiTimeWindowUnit.MONTHS),
        )
    with pytest.raises(ValueError, match='minutes, hours, and days'):
        SourceRequirement(
            source=KpiSource.PI_INTERPOLATED,
            partition=KpiPartition.MONTHLY,
            columns=('tag_a',),
            time_window=KpiTimeWindow(3, KpiTimeWindowUnit.HOURS),
        )
    with pytest.raises(ValueError, match='requires daily partition'):
        SourceRequirement(
            source=KpiSource.PI_INTERPOLATED,
            partition=KpiPartition.MONTHLY,
            columns=('tag_a',),
            operational_scope=KpiOperationalScope.CURRENT_OPERATIONAL_DAY_MINE,
        )


def test_shift_selector_is_separate_from_pi_operational_scopes() -> None:
    requirement = SourceRequirement(
        source=KpiSource.DISPATCH_STD_SHIFT_STATE,
        partition=KpiPartition.SHIFT,
        columns=('state',),
        shift=ShiftSelection(ShiftScope.DAYS, days=3),
    )
    assert requirement.shift == ShiftSelection(ShiftScope.DAYS, days=3)

    with pytest.raises(ValueError, match='cannot mix'):
        SourceRequirement(
            source=KpiSource.DISPATCH_STD_SHIFT_STATE,
            partition=KpiPartition.SHIFT,
            columns=('state',),
            time_window=KpiTimeWindow(2, KpiTimeWindowUnit.HOURS),
            shift=ShiftSelection(ShiftScope.CURRENT),
        )


def test_source_requirement_rejects_empty_or_duplicate_columns() -> None:
    with pytest.raises(ValueError, match='at least one column'):
        SourceRequirement(
            source=KpiSource.REMANENTES_STOCKS,
            partition=KpiPartition.LATEST,
            columns=(),
        )
    with pytest.raises(ValueError, match='unique'):
        SourceRequirement(
            source=KpiSource.REMANENTES_STOCKS,
            partition=KpiPartition.LATEST,
            columns=('a', 'a'),
        )
