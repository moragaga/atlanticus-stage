from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ada.data.core import (
    DataColumn,
    DataColumnType,
    DataPartition,
    DataRequirement,
    DataSource,
    DataSourceView,
    OperationalScope,
    ShiftScope,
    ShiftSelection,
    TimeWindow,
    TimeWindowUnit,
)


def test_time_window_keeps_fixed_delta_semantics_and_calendar_months() -> None:
    assert TimeWindow(30, TimeWindowUnit.MINUTES).to_timedelta() == timedelta(minutes=30)
    assert TimeWindow(2, TimeWindowUnit.HOURS).to_timedelta() == timedelta(hours=2)
    assert TimeWindow(3, TimeWindowUnit.DAYS).to_timedelta() == timedelta(days=3)

    monthly = TimeWindow(1, TimeWindowUnit.MONTHS)
    with pytest.raises(ValueError, match='no fixed timedelta'):
        monthly.to_timedelta()
    assert monthly.start_from(datetime(2026, 3, 31, 12, tzinfo=UTC)) == datetime(
        2026, 2, 28, 12, tzinfo=UTC
    )


def test_time_window_requires_typed_unit() -> None:
    with pytest.raises(TypeError, match='TimeWindowUnit'):
        TimeWindow(2, 'hours')  # type: ignore[arg-type]


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


def test_data_column_requires_explicit_canonical_type() -> None:
    assert DataColumn(' tag_a ', DataColumnType.FLOAT) == DataColumn('tag_a', DataColumnType.FLOAT)
    with pytest.raises(TypeError, match='DataColumnType'):
        DataColumn('tag_a', 'float')  # type: ignore[arg-type]
    with pytest.raises(ValueError, match='non-empty'):
        DataColumn(' ', DataColumnType.TEXT)


def test_data_column_types_cover_current_operational_schema_contract() -> None:
    assert {item.value for item in DataColumnType} == {
        'text',
        'integer',
        'float',
        'boolean',
        'date',
        'datetime',
    }


def test_source_requirement_has_typed_source_partition_and_exact_view_identity() -> None:
    requirement = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.DAILY,
        columns=(
            DataColumn('tag_a', DataColumnType.FLOAT),
            DataColumn('tag_b', DataColumnType.TEXT),
        ),
        time_window=TimeWindow(2, TimeWindowUnit.HOURS),
    )

    assert requirement.column_names == ('tag_a', 'tag_b')
    assert requirement.columns[0].data_type is DataColumnType.FLOAT
    assert requirement.columns[1].data_type is DataColumnType.TEXT
    assert requirement.view == DataSourceView(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.DAILY,
    )


def test_pi_partition_contract_rejects_invalid_combinations() -> None:
    DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(DataColumn('tag_a', DataColumnType.FLOAT),),
    )
    DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.DAILY,
        columns=(DataColumn('tag_a', DataColumnType.FLOAT),),
        operational_scope=OperationalScope.CURRENT_TURN_PLANT,
    )
    DataRequirement(
        source=DataSource.PI_RECORDED,
        partition=DataPartition.MONTHLY,
        columns=(DataColumn('tag_a', DataColumnType.FLOAT),),
        time_window=TimeWindow(2, TimeWindowUnit.MONTHS),
    )

    with pytest.raises(ValueError, match='latest partition must not declare'):
        DataRequirement(
            source=DataSource.PI_INTERPOLATED,
            partition=DataPartition.LATEST,
            columns=(DataColumn('tag_a', DataColumnType.FLOAT),),
            time_window=TimeWindow(2, TimeWindowUnit.HOURS),
        )
    with pytest.raises(ValueError, match='unsupported partition'):
        DataRequirement(
            source=DataSource.PI_RECORDED,
            partition=DataPartition.LATEST,
            columns=(DataColumn('tag_a', DataColumnType.FLOAT),),
        )
    with pytest.raises(ValueError, match='months time window requires monthly'):
        DataRequirement(
            source=DataSource.PI_INTERPOLATED,
            partition=DataPartition.DAILY,
            columns=(DataColumn('tag_a', DataColumnType.FLOAT),),
            time_window=TimeWindow(1, TimeWindowUnit.MONTHS),
        )
    with pytest.raises(ValueError, match='minutes, hours, and days'):
        DataRequirement(
            source=DataSource.PI_INTERPOLATED,
            partition=DataPartition.MONTHLY,
            columns=(DataColumn('tag_a', DataColumnType.FLOAT),),
            time_window=TimeWindow(3, TimeWindowUnit.HOURS),
        )
    with pytest.raises(ValueError, match='requires daily partition'):
        DataRequirement(
            source=DataSource.PI_INTERPOLATED,
            partition=DataPartition.MONTHLY,
            columns=(DataColumn('tag_a', DataColumnType.FLOAT),),
            operational_scope=OperationalScope.CURRENT_OPERATIONAL_DAY_MINE,
        )


def test_shift_selector_is_separate_from_pi_operational_scopes() -> None:
    requirement = DataRequirement(
        source=DataSource.DISPATCH_STD_SHIFT_STATE,
        partition=DataPartition.SHIFT,
        columns=(DataColumn('state', DataColumnType.TEXT),),
        shift=ShiftSelection(ShiftScope.DAYS, days=3),
    )
    assert requirement.shift == ShiftSelection(ShiftScope.DAYS, days=3)

    with pytest.raises(ValueError, match='cannot mix'):
        DataRequirement(
            source=DataSource.DISPATCH_STD_SHIFT_STATE,
            partition=DataPartition.SHIFT,
            columns=(DataColumn('state', DataColumnType.TEXT),),
            time_window=TimeWindow(2, TimeWindowUnit.HOURS),
            shift=ShiftSelection(ShiftScope.CURRENT),
        )


def test_source_requirement_rejects_empty_or_duplicate_columns() -> None:
    with pytest.raises(ValueError, match='at least one column'):
        DataRequirement(
            source=DataSource.REMANENTES_STOCKS,
            partition=DataPartition.LATEST,
            columns=(),
        )
    with pytest.raises(ValueError, match='unique'):
        DataRequirement(
            source=DataSource.REMANENTES_STOCKS,
            partition=DataPartition.LATEST,
            columns=(
                DataColumn('a', DataColumnType.FLOAT),
                DataColumn('a', DataColumnType.TEXT),
            ),
        )
    with pytest.raises(TypeError, match='DataColumn'):
        DataRequirement(
            source=DataSource.REMANENTES_STOCKS,
            partition=DataPartition.LATEST,
            columns=('a',),  # type: ignore[arg-type]
        )
