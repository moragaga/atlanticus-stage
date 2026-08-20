from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from ada.kpis.core import KpiSourceView, KpiWatermark
from ada.kpis.planner import KpiLoadPlan, KpiSourceViewLoadPlan
from ada.kpis.sources.bindings import (
    KpiPartitionBinding,
    KpiSourceBinding,
    KpiSourceRegistry,
    TimePartitionGranularity,
)
from ada.kpis.sources.errors import KpiSourceSchemaError, KpiSourcesError
from ada.kpis.sources.loaded import (
    KpiSourceLoadFailure,
    LoadedKpiSources,
    LoadedKpiSourceView,
)
from ada.kpis.sources.operational import KpiOperationalWindowResolver
from ada.kpis.sources.reader import SourceDatasetReader
from ada.kpis.sources.shifts import MineShiftResolver


class KpiSourceLoader:
    def __init__(
        self,
        *,
        reader: SourceDatasetReader,
        registry: KpiSourceRegistry,
        shift_resolver: MineShiftResolver | None = None,
        operational_resolver: KpiOperationalWindowResolver | None = None,
    ) -> None:
        if not isinstance(reader, SourceDatasetReader):
            raise TypeError('reader must implement SourceDatasetReader')
        if not isinstance(registry, KpiSourceRegistry):
            raise TypeError('registry must be KpiSourceRegistry')
        if shift_resolver is not None and not isinstance(shift_resolver, MineShiftResolver):
            raise TypeError('shift_resolver must be MineShiftResolver or None')
        if operational_resolver is not None and not isinstance(
            operational_resolver, KpiOperationalWindowResolver
        ):
            raise TypeError('operational_resolver must be KpiOperationalWindowResolver or None')
        self._reader = reader
        self._registry = registry
        self._shift_resolver = shift_resolver or MineShiftResolver()
        self._operational_resolver = operational_resolver or KpiOperationalWindowResolver()

    def load(self, *, plan: KpiLoadPlan, watermark: KpiWatermark) -> LoadedKpiSources:
        if not isinstance(plan, KpiLoadPlan):
            raise TypeError('plan must be KpiLoadPlan')
        if not isinstance(watermark, KpiWatermark):
            raise TypeError('watermark must be KpiWatermark')
        loaded: dict[KpiSourceView, LoadedKpiSourceView] = {}
        failures: dict[KpiSourceView, KpiSourceLoadFailure] = {}
        for view_plan in plan.views:
            try:
                binding, partition_binding = self._registry.get_view(view_plan.view)
                loaded[view_plan.view] = LoadedKpiSourceView(
                    view=view_plan.view,
                    frame=self._load_view(
                        plan=view_plan,
                        binding=binding,
                        partition_binding=partition_binding,
                        watermark=watermark,
                    ),
                )
            except KpiSourcesError as error:
                failures[view_plan.view] = KpiSourceLoadFailure(
                    view=view_plan.view,
                    message=str(error),
                )
        return LoadedKpiSources(
            watermark=watermark,
            plan=plan,
            registry=self._registry,
            loaded=loaded,
            failures=failures,
            shift_resolver=self._shift_resolver,
            operational_resolver=self._operational_resolver,
        )

    def _load_view(
        self,
        *,
        plan: KpiSourceViewLoadPlan,
        binding: KpiSourceBinding,
        partition_binding: KpiPartitionBinding,
        watermark: KpiWatermark,
    ) -> pd.DataFrame:
        columns = _with_technical(
            plan.columns,
            partition_binding.timestamp_column,
            partition_binding.shift_column,
        )
        if partition_binding.shift_column is not None:
            return self._load_shift_view(
                plan=plan,
                binding=binding,
                partition_binding=partition_binding,
                columns=columns,
                watermark=watermark,
            )
        if partition_binding.time_partition_granularity is not None:
            return self._load_time_view(
                plan=plan,
                binding=binding,
                partition_binding=partition_binding,
                columns=columns,
                watermark=watermark,
            )
        target = binding.definition.resolve_target(
            materialization=partition_binding.materialization
        )
        frame = self._reader.read_frame(
            definition=binding.definition,
            target=target,
            columns=columns,
        )
        return _empty_or_copy(frame, columns)

    def _load_time_view(
        self,
        *,
        plan: KpiSourceViewLoadPlan,
        binding: KpiSourceBinding,
        partition_binding: KpiPartitionBinding,
        columns: tuple[str, ...],
        watermark: KpiWatermark,
    ) -> pd.DataFrame:
        start_utc, end_utc = self._load_bounds(plan=plan, watermark=watermark)
        assert partition_binding.time_partition_granularity is not None
        frames: list[pd.DataFrame] = []
        for partition in _time_partitions(
            start=start_utc,
            end=end_utc,
            granularity=partition_binding.time_partition_granularity,
        ):
            target = binding.definition.resolve_target(
                materialization=partition_binding.materialization,
                partition=partition,
            )
            frame = self._reader.read_frame(
                definition=binding.definition,
                target=target,
                columns=columns,
                timestamp_column=partition_binding.timestamp_column,
                start_utc=start_utc,
                end_utc=end_utc,
            )
            if frame is not None:
                frames.append(frame)
        combined = _concat_or_empty(frames, columns)
        return _sort_time(
            combined,
            source=plan.source.value,
            column=partition_binding.timestamp_column,
        )

    def _load_shift_view(
        self,
        *,
        plan: KpiSourceViewLoadPlan,
        binding: KpiSourceBinding,
        partition_binding: KpiPartitionBinding,
        columns: tuple[str, ...],
        watermark: KpiWatermark,
    ) -> pd.DataFrame:
        turns = []
        for selection in plan.shifts:
            for turn in self._shift_resolver.resolve(selection=selection, watermark=watermark):
                if turn.shift_id not in {item.shift_id for item in turns}:
                    turns.append(turn)
        frames: list[pd.DataFrame] = []
        for turn in turns:
            target = binding.definition.resolve_target(
                materialization=partition_binding.materialization,
                partition=turn.partition,
            )
            frame = self._reader.read_frame(
                definition=binding.definition,
                target=target,
                columns=columns,
            )
            if frame is not None:
                frames.append(frame)
        return _concat_or_empty(frames, columns)

    def _load_bounds(
        self,
        *,
        plan: KpiSourceViewLoadPlan,
        watermark: KpiWatermark,
    ) -> tuple[datetime, datetime]:
        starts = [window.start_from(watermark.timestamp_utc) for window in plan.time_windows]
        ends = [watermark.timestamp_utc for _ in plan.time_windows]
        for scope in plan.operational_scopes:
            window = self._operational_resolver.resolve(scope=scope, watermark=watermark)
            starts.append(window.start_utc)
            ends.append(window.end_utc)
        if not starts:
            raise KpiSourceSchemaError(
                f'{plan.source.value}/{plan.partition.value}: time partition requires a selector'
            )
        return min(starts), max(ends)


def _sort_time(frame: pd.DataFrame, *, source: str, column: str | None) -> pd.DataFrame:
    if column is None:
        raise KpiSourceSchemaError(f'{source}: partitioned time view has no timestamp column')
    if column not in frame.columns:
        raise KpiSourceSchemaError(f'{source}: timestamp column is missing: {column}')
    if frame.empty:
        return frame.reset_index(drop=True)
    parsed = pd.to_datetime(frame[column], utc=True, errors='coerce')
    if parsed.isna().any():
        raise KpiSourceSchemaError(f'{source}: timestamp column contains invalid values')
    output = frame.copy(deep=False)
    output[column] = parsed
    return output.sort_values(column, kind='stable').reset_index(drop=True)


def _empty_or_copy(frame: pd.DataFrame | None, columns: tuple[str, ...]) -> pd.DataFrame:
    if frame is None:
        return pd.DataFrame(columns=list(columns))
    missing = tuple(column for column in columns if column not in frame.columns)
    if missing:
        raise KpiSourceSchemaError(f'loaded dataframe is missing columns: {missing}')
    return frame.loc[:, list(columns)].copy(deep=False).reset_index(drop=True)


def _concat_or_empty(frames: list[pd.DataFrame], columns: tuple[str, ...]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame(columns=list(columns))
    for frame in frames:
        missing = tuple(column for column in columns if column not in frame.columns)
        if missing:
            raise KpiSourceSchemaError(f'loaded dataframe is missing columns: {missing}')
    return pd.concat(
        (frame.loc[:, list(columns)] for frame in frames),
        ignore_index=True,
        sort=False,
    )


def _with_technical(
    columns: tuple[str, ...],
    *technical_columns: str | None,
) -> tuple[str, ...]:
    output = list(columns)
    for technical in technical_columns:
        if technical is not None and technical not in output:
            output.append(technical)
    return tuple(output)


def _time_partitions(
    *,
    start: datetime,
    end: datetime,
    granularity: TimePartitionGranularity,
) -> tuple[dict[str, str], ...]:
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    if granularity is TimePartitionGranularity.DAY:
        current = start_utc.date()
        output: list[dict[str, str]] = []
        while current <= end_utc.date():
            output.append(_day_partition(current))
            current = date.fromordinal(current.toordinal() + 1)
        return tuple(output)
    current_year = start_utc.year
    current_month = start_utc.month
    output = []
    while (current_year, current_month) <= (end_utc.year, end_utc.month):
        output.append({'year': f'{current_year:04d}', 'month': f'{current_month:02d}'})
        if current_month == 12:
            current_year += 1
            current_month = 1
        else:
            current_month += 1
    return tuple(output)


def _day_partition(value: date) -> dict[str, str]:
    return {
        'year': f'{value.year:04d}',
        'month': f'{value.month:02d}',
        'day': f'{value.day:02d}',
    }
