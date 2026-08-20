from ada.kpis.core import (
    KpiArea,
    KpiCatalog,
    KpiMode,
    KpiPartition,
    KpiSource,
    KpiSpec,
    KpiTimeWindow,
    KpiTimeWindowUnit,
    ShiftScope,
    ShiftSelection,
    SourceRequirement,
)
from ada.kpis.planner import KpiRequirementPlanner


def _resolver(_):
    return None


def test_planner_merges_columns_and_selectors_per_source_partition() -> None:
    three_days = KpiTimeWindow(3, KpiTimeWindowUnit.DAYS)
    two_hours = KpiTimeWindow(2, KpiTimeWindowUnit.HOURS)
    catalog = KpiCatalog(
        specs=(
            KpiSpec(
                key='a',
                area=KpiArea.MINA,
                mode=KpiMode.CUSTOM,
                source=KpiSource.PI_INTERPOLATED,
                partition=KpiPartition.DAILY,
                columns=('a', 'b', 'c', 'd', 'e'),
                time_window=three_days,
                custom_resolver=_resolver,
            ),
            KpiSpec(
                key='b',
                area=KpiArea.MINA,
                mode=KpiMode.CUSTOM,
                source=KpiSource.PI_INTERPOLATED,
                partition=KpiPartition.DAILY,
                columns=('a', 'c', 'e'),
                time_window=two_hours,
                custom_resolver=_resolver,
            ),
        )
    )

    plan = KpiRequirementPlanner().plan(catalog)

    daily = plan.view_plan(KpiSource.PI_INTERPOLATED, KpiPartition.DAILY)
    assert daily.columns == ('a', 'b', 'c', 'd', 'e')
    assert daily.time_windows == (three_days, two_hours)
    exact = plan.requirements_for('b')[0]
    assert exact.columns == ('a', 'c', 'e')
    assert exact.time_window is two_hours


def test_planner_keeps_latest_daily_and_shift_as_distinct_views() -> None:
    current = ShiftSelection(ShiftScope.CURRENT)
    days = ShiftSelection(ShiftScope.DAYS, days=3)
    catalog = KpiCatalog(
        specs=(
            KpiSpec(
                key='latest',
                area=KpiArea.MINA,
                mode=KpiMode.LATEST_NUMBER,
                source=KpiSource.PI_INTERPOLATED,
                partition=KpiPartition.LATEST,
                columns=('latest',),
            ),
            KpiSpec(
                key='daily',
                area=KpiArea.MINA,
                mode=KpiMode.CUSTOM,
                source=KpiSource.PI_INTERPOLATED,
                partition=KpiPartition.DAILY,
                columns=('series',),
                time_window=KpiTimeWindow(90, KpiTimeWindowUnit.MINUTES),
                custom_resolver=_resolver,
            ),
            KpiSpec(
                key='shift_a',
                area=KpiArea.MINA,
                mode=KpiMode.CUSTOM,
                source=KpiSource.DISPATCH_STD_SHIFT_STATE,
                partition=KpiPartition.SHIFT,
                columns=('state',),
                shift=current,
                custom_resolver=_resolver,
            ),
            KpiSpec(
                key='shift_b',
                area=KpiArea.MINA,
                mode=KpiMode.CUSTOM,
                source=KpiSource.DISPATCH_STD_SHIFT_STATE,
                partition=KpiPartition.SHIFT,
                columns=('eqmt', 'state'),
                shift=days,
                custom_resolver=_resolver,
            ),
        )
    )

    plan = KpiRequirementPlanner().plan(catalog)

    assert plan.view_plan(KpiSource.PI_INTERPOLATED, KpiPartition.LATEST).columns == ('latest',)
    assert plan.view_plan(KpiSource.PI_INTERPOLATED, KpiPartition.DAILY).columns == ('series',)
    dispatch = plan.view_plan(KpiSource.DISPATCH_STD_SHIFT_STATE, KpiPartition.SHIFT)
    assert dispatch.columns == ('state', 'eqmt')
    assert dispatch.shifts == (current, days)
    assert plan.sources == (
        KpiSource.PI_INTERPOLATED,
        KpiSource.DISPATCH_STD_SHIFT_STATE,
    )


def test_same_kpi_can_request_two_partitions_of_same_source() -> None:
    latest = SourceRequirement(
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.LATEST,
        columns=('current',),
    )
    daily = SourceRequirement(
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.DAILY,
        columns=('series',),
        time_window=KpiTimeWindow(2, KpiTimeWindowUnit.HOURS),
    )
    spec = KpiSpec(
        key='multi',
        area=KpiArea.GENERAL,
        mode=KpiMode.CUSTOM,
        source_requirements=(latest, daily),
        custom_resolver=_resolver,
    )

    plan = KpiRequirementPlanner().plan(KpiCatalog(specs=(spec,)))

    assert plan.requirements_for('multi') == (latest, daily)
    assert tuple(view.partition for view in plan.views) == (
        KpiPartition.LATEST,
        KpiPartition.DAILY,
    )
