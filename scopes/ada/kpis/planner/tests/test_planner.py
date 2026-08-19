from datetime import timedelta

from ada.kpis.core import (
    KpiArea,
    KpiCatalog,
    KpiMode,
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


def test_planner_widens_physical_time_load_and_keeps_exact_requirements() -> None:
    three_days = KpiTimeWindow(3, KpiTimeWindowUnit.DAYS)
    two_hours = KpiTimeWindow(2, KpiTimeWindowUnit.HOURS)
    first = KpiSpec(
        key='a',
        area=KpiArea.MINA,
        mode=KpiMode.CUSTOM,
        source=KpiSource.PI_INTERPOLATED,
        columns=('a', 'b', 'c', 'd', 'e'),
        time_window=three_days,
        custom_resolver=_resolver,
    )
    second = KpiSpec(
        key='b',
        area=KpiArea.MINA,
        mode=KpiMode.CUSTOM,
        source=KpiSource.PI_INTERPOLATED,
        columns=('a', 'c', 'e'),
        time_window=two_hours,
        custom_resolver=_resolver,
    )

    plan = KpiRequirementPlanner().plan(KpiCatalog(specs=(first, second)))

    source_plan = plan.source_plan(KpiSource.PI_INTERPOLATED)
    assert source_plan.time_columns == ('a', 'b', 'c', 'd', 'e')
    assert source_plan.time_window is three_days
    assert source_plan.time_window.to_timedelta() == timedelta(days=3)
    assert plan.requirements_for('b')[KpiSource.PI_INTERPOLATED].columns == ('a', 'c', 'e')
    assert plan.requirements_for('b')[KpiSource.PI_INTERPOLATED].time_window is two_hours


def test_planner_separates_snapshot_time_and_shift_load_groups() -> None:
    current = ShiftSelection(ShiftScope.CURRENT)
    days = ShiftSelection(ShiftScope.DAYS, days=3)
    catalog = KpiCatalog(
        specs=(
            KpiSpec(
                key='snapshot',
                area=KpiArea.MINA,
                mode=KpiMode.LATEST_NUMBER,
                source=KpiSource.PI_INTERPOLATED,
                columns=('latest',),
            ),
            KpiSpec(
                key='time',
                area=KpiArea.MINA,
                mode=KpiMode.CUSTOM,
                source=KpiSource.PI_INTERPOLATED,
                columns=('series',),
                time_window=KpiTimeWindow(90, KpiTimeWindowUnit.MINUTES),
                custom_resolver=_resolver,
            ),
            KpiSpec(
                key='shift_a',
                area=KpiArea.MINA,
                mode=KpiMode.CUSTOM,
                source=KpiSource.DISPATCH_STD_SHIFT_STATE,
                columns=('state',),
                shift=current,
                custom_resolver=_resolver,
            ),
            KpiSpec(
                key='shift_b',
                area=KpiArea.MINA,
                mode=KpiMode.CUSTOM,
                source=KpiSource.DISPATCH_STD_SHIFT_STATE,
                columns=('eqmt', 'state'),
                shift=days,
                custom_resolver=_resolver,
            ),
        )
    )

    plan = KpiRequirementPlanner().plan(catalog)

    pi = plan.source_plan(KpiSource.PI_INTERPOLATED)
    assert pi.snapshot_columns == ('latest',)
    assert pi.time_columns == ('series',)
    dispatch = plan.source_plan(KpiSource.DISPATCH_STD_SHIFT_STATE)
    assert dispatch.shift_columns == ('state', 'eqmt')
    assert dispatch.shifts == (current, days)


def test_requirements_by_source_remain_unmerged_for_each_rule() -> None:
    spec = KpiSpec(
        key='multi',
        area=KpiArea.GENERAL,
        mode=KpiMode.CUSTOM,
        requirements_by_source={
            KpiSource.PI_INTERPOLATED: SourceRequirement(
                columns=('pi_a',),
                time_window=KpiTimeWindow(2, KpiTimeWindowUnit.HOURS),
            ),
            KpiSource.REMANENTES_STOCKS: SourceRequirement(columns=('stock_3080',)),
        },
        custom_resolver=_resolver,
    )

    plan = KpiRequirementPlanner().plan(KpiCatalog(specs=(spec,)))

    exact = plan.requirements_for('multi')
    assert exact[KpiSource.PI_INTERPOLATED].columns == ('pi_a',)
    assert exact[KpiSource.REMANENTES_STOCKS].columns == ('stock_3080',)
