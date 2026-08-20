from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import pandas as pd
import pytest

from ada.kpis.core import (
    KpiArea,
    KpiCatalog,
    KpiMode,
    KpiPartition,
    KpiSource,
    KpiSourceNotRequestedError,
    KpiSpec,
    KpiTimeWindow,
    KpiTimeWindowUnit,
    KpiWatermark,
    ShiftScope,
    ShiftSelection,
    SourceRequirement,
)
from ada.kpis.planner import KpiRequirementPlanner
from ada.kpis.sources import (
    KpiPartitionBinding,
    KpiSourceBinding,
    KpiSourceLoader,
    KpiSourceRegistry,
    KpiSourceSchemaError,
    KpiSourceUnavailableError,
    TimePartitionGranularity,
)
from atlanticus.datasets.layouts import SingleArtifactLayout
from atlanticus.datasets.models import (
    DatasetDefinition,
    DatasetKey,
    DatasetTarget,
    MaterializationDefinition,
)


def _resolver(_):
    return None


class FakeReader:
    def __init__(
        self,
        frames: Mapping[str, pd.DataFrame],
        failures: Mapping[str, Exception] | None = None,
    ) -> None:
        self.frames = dict(frames)
        self.failures = {} if failures is None else dict(failures)
        self.calls: list[tuple[str, tuple[str, ...], datetime | None, datetime | None]] = []

    def read_frame(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        columns: tuple[str, ...],
        timestamp_column: str | None = None,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
    ) -> pd.DataFrame | None:
        identifier = target.identifier
        self.calls.append((identifier, columns, start_utc, end_utc))
        failure = self.failures.get(identifier)
        if failure is not None:
            raise failure
        source = self.frames.get(identifier)
        if source is None:
            return None
        missing = tuple(column for column in columns if column not in source.columns)
        if missing:
            raise KpiSourceSchemaError(f'{identifier}: missing={missing}')
        frame = source.loc[:, list(columns)].copy()
        if timestamp_column is not None and start_utc is not None and end_utc is not None:
            timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors='coerce')
            frame = frame.loc[
                (timestamps >= pd.Timestamp(start_utc)) & (timestamps <= pd.Timestamp(end_utc))
            ]
        return frame.reset_index(drop=True)


def _pi_binding() -> KpiSourceBinding:
    definition = DatasetDefinition(
        key=DatasetKey(namespace=('pi', 'web-api'), name='interpolated'),
        materializations=(
            MaterializationDefinition(name='latest', layout=SingleArtifactLayout()),
            MaterializationDefinition(
                name='daily',
                layout=SingleArtifactLayout(),
                partition_dimensions=('year', 'month', 'day'),
            ),
            MaterializationDefinition(
                name='monthly',
                layout=SingleArtifactLayout(),
                partition_dimensions=('year', 'month'),
            ),
        ),
    )
    return KpiSourceBinding(
        source=KpiSource.PI_INTERPOLATED,
        definition=definition,
        partitions={
            KpiPartition.LATEST: KpiPartitionBinding(
                partition=KpiPartition.LATEST,
                materialization='latest',
                timestamp_column='timestamp_utc',
            ),
            KpiPartition.DAILY: KpiPartitionBinding(
                partition=KpiPartition.DAILY,
                materialization='daily',
                time_partition_granularity=TimePartitionGranularity.DAY,
                timestamp_column='timestamp_utc',
            ),
            KpiPartition.MONTHLY: KpiPartitionBinding(
                partition=KpiPartition.MONTHLY,
                materialization='monthly',
                time_partition_granularity=TimePartitionGranularity.MONTH,
                timestamp_column='timestamp_utc',
            ),
        },
    )


def _shift_binding() -> KpiSourceBinding:
    definition = DatasetDefinition(
        key=DatasetKey(namespace=('dispatch',), name='std_shift_state'),
        materializations=(
            MaterializationDefinition(
                name='shift',
                layout=SingleArtifactLayout(),
                partition_dimensions=('year', 'month', 'day', 'turn'),
            ),
        ),
    )
    return KpiSourceBinding(
        source=KpiSource.DISPATCH_STD_SHIFT_STATE,
        definition=definition,
        partitions={
            KpiPartition.SHIFT: KpiPartitionBinding(
                partition=KpiPartition.SHIFT,
                materialization='shift',
                shift_column='shift_id',
            )
        },
    )


def test_loader_overfetches_daily_once_but_each_context_gets_exact_window_and_columns() -> None:
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
    binding = _pi_binding()
    frames = {}
    for day in (16, 17, 18, 19):
        target = binding.definition.resolve_target(
            materialization='daily',
            partition={'year': '2026', 'month': '08', 'day': f'{day:02d}'},
        )
        frames[target.identifier] = pd.DataFrame(
            {
                'timestamp_utc': [datetime(2026, 8, day, 10, tzinfo=UTC)],
                'a': [day],
                'b': [day + 1],
                'c': [day + 2],
                'd': [day + 3],
                'e': [day + 4],
            }
        )
    final_target = binding.definition.resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '19'},
    )
    frames[final_target.identifier] = pd.concat(
        (
            frames[final_target.identifier],
            pd.DataFrame(
                {
                    'timestamp_utc': [datetime(2026, 8, 19, 11, 30, tzinfo=UTC)],
                    'a': [100],
                    'b': [101],
                    'c': [102],
                    'd': [103],
                    'e': [104],
                }
            ),
        ),
        ignore_index=True,
    )
    reader = FakeReader(frames)
    loader = KpiSourceLoader(
        reader=reader,
        registry=KpiSourceRegistry({KpiSource.PI_INTERPOLATED: binding}),
    )
    watermark = KpiWatermark(datetime(2026, 8, 19, 12, 0, tzinfo=UTC))

    loaded = loader.load(plan=plan, watermark=watermark)
    pi = loaded.context_for('b').get(KpiSource.PI_INTERPOLATED, KpiPartition.DAILY)

    assert list(pi.dataframe.columns) == ['a', 'c', 'e']
    assert len(pi.dataframe) == 2
    assert pi.last_row().to_dict() == {'a': 100, 'c': 102, 'e': 104}
    assert all(call[1] == ('a', 'b', 'c', 'd', 'e', 'timestamp_utc') for call in reader.calls)


def test_same_source_latest_and_daily_are_loaded_as_independent_views() -> None:
    latest_requirement = SourceRequirement(
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.LATEST,
        columns=('current',),
    )
    daily_requirement = SourceRequirement(
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.DAILY,
        columns=('series',),
        time_window=KpiTimeWindow(2, KpiTimeWindowUnit.HOURS),
    )
    spec = KpiSpec(
        key='multi',
        area=KpiArea.MINA,
        mode=KpiMode.CUSTOM,
        source_requirements=(latest_requirement, daily_requirement),
        custom_resolver=_resolver,
    )
    plan = KpiRequirementPlanner().plan(KpiCatalog(specs=(spec,)))
    binding = _pi_binding()
    latest_target = binding.definition.resolve_target(materialization='latest')
    daily_target = binding.definition.resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '19'},
    )
    reader = FakeReader(
        {
            latest_target.identifier: pd.DataFrame(
                {'timestamp_utc': ['2026-08-19T12:00:00Z'], 'current': [9]}
            ),
            daily_target.identifier: pd.DataFrame(
                {
                    'timestamp_utc': ['2026-08-19T11:00:00Z'],
                    'series': [7],
                }
            ),
        }
    )
    loaded = KpiSourceLoader(
        reader=reader,
        registry=KpiSourceRegistry({KpiSource.PI_INTERPOLATED: binding}),
    ).load(
        plan=plan,
        watermark=KpiWatermark(datetime(2026, 8, 19, 12, tzinfo=UTC)),
    )

    context = loaded.context_for('multi')
    assert context.get(KpiSource.PI_INTERPOLATED, KpiPartition.LATEST).last_value('current') == 9
    assert context.get(KpiSource.PI_INTERPOLATED, KpiPartition.DAILY).last_value('series') == 7


def test_monthly_month_window_reads_calendar_month_partitions() -> None:
    spec = KpiSpec(
        key='monthly',
        area=KpiArea.PLANTA,
        mode=KpiMode.CUSTOM,
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.MONTHLY,
        columns=('value',),
        time_window=KpiTimeWindow(2, KpiTimeWindowUnit.MONTHS),
        custom_resolver=_resolver,
    )
    plan = KpiRequirementPlanner().plan(KpiCatalog(specs=(spec,)))
    binding = _pi_binding()
    frames = {}
    for month in (6, 7, 8):
        target = binding.definition.resolve_target(
            materialization='monthly',
            partition={'year': '2026', 'month': f'{month:02d}'},
        )
        frames[target.identifier] = pd.DataFrame(
            {'timestamp_utc': [datetime(2026, month, 15, tzinfo=UTC)], 'value': [month]}
        )
    reader = FakeReader(frames)

    loaded = KpiSourceLoader(
        reader=reader,
        registry=KpiSourceRegistry({KpiSource.PI_INTERPOLATED: binding}),
    ).load(
        plan=plan,
        watermark=KpiWatermark(datetime(2026, 8, 31, 12, tzinfo=UTC)),
    )

    frame = loaded.context_for('monthly').get(
        KpiSource.PI_INTERPOLATED,
        KpiPartition.MONTHLY,
    )
    assert frame.dataframe['value'].tolist() == [7, 8]
    assert len(reader.calls) == 3


def test_missing_physical_partitions_become_empty_exact_context() -> None:
    spec = KpiSpec(
        key='empty',
        area=KpiArea.MINA,
        mode=KpiMode.CUSTOM,
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.DAILY,
        columns=('a', 'b'),
        time_window=KpiTimeWindow(2, KpiTimeWindowUnit.HOURS),
        custom_resolver=_resolver,
    )
    plan = KpiRequirementPlanner().plan(KpiCatalog(specs=(spec,)))
    binding = _pi_binding()
    loader = KpiSourceLoader(
        reader=FakeReader({}),
        registry=KpiSourceRegistry({KpiSource.PI_INTERPOLATED: binding}),
    )

    loaded = loader.load(
        plan=plan,
        watermark=KpiWatermark(datetime(2026, 8, 19, 12, 0, tzinfo=UTC)),
    )
    frame = loaded.context_for('empty').get(KpiSource.PI_INTERPOLATED, KpiPartition.DAILY)

    assert frame.dataframe.empty
    assert list(frame.dataframe.columns) == ['a', 'b']


def test_source_failure_is_isolated_until_dependent_kpi_requests_view() -> None:
    pi_spec = KpiSpec(
        key='pi',
        area=KpiArea.MINA,
        mode=KpiMode.LATEST_NUMBER,
        source=KpiSource.PI_INTERPOLATED,
        partition=KpiPartition.LATEST,
        columns=('a',),
    )
    shift_spec = KpiSpec(
        key='dispatch',
        area=KpiArea.MINA,
        mode=KpiMode.CUSTOM,
        source=KpiSource.DISPATCH_STD_SHIFT_STATE,
        partition=KpiPartition.SHIFT,
        columns=('state',),
        shift=ShiftSelection(ShiftScope.CURRENT),
        custom_resolver=_resolver,
    )
    plan = KpiRequirementPlanner().plan(KpiCatalog(specs=(pi_spec, shift_spec)))
    pi_binding = _pi_binding()
    shift_binding = _shift_binding()
    latest_target = pi_binding.definition.resolve_target(materialization='latest')
    current_target = shift_binding.definition.resolve_target(
        materialization='shift',
        partition={'year': '2026', 'month': '08', 'day': '20', 'turn': '001'},
    )
    reader = FakeReader(
        {
            latest_target.identifier: pd.DataFrame(
                {'timestamp_utc': ['2026-08-20T02:00:00Z'], 'a': [5]}
            )
        },
        failures={current_target.identifier: KpiSourceSchemaError('bad dispatch schema')},
    )
    loader = KpiSourceLoader(
        reader=reader,
        registry=KpiSourceRegistry(
            {
                KpiSource.PI_INTERPOLATED: pi_binding,
                KpiSource.DISPATCH_STD_SHIFT_STATE: shift_binding,
            }
        ),
    )

    loaded = loader.load(
        plan=plan,
        watermark=KpiWatermark(datetime(2026, 8, 20, 2, 0, tzinfo=UTC)),
    )

    assert (
        loaded.context_for('pi')
        .get(KpiSource.PI_INTERPOLATED, KpiPartition.LATEST)
        .last_value_number('a')
        == 5.0
    )
    with pytest.raises(KpiSourceUnavailableError):
        loaded.context_for('dispatch')
    with pytest.raises(KpiSourceNotRequestedError):
        loaded.context_for('pi').get(KpiSource.PI_INTERPOLATED, KpiPartition.DAILY)
