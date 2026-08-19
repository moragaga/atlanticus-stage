from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from ada.kpis.core import KpiSource, KpiWatermark
from ada.kpis.planner import KpiLoadPlan, KpiSourceLoadPlan
from ada.kpis.sources.bindings import (
    KpiSourceBinding,
    KpiSourceRegistry,
    TimePartitionGranularity,
)
from ada.kpis.sources.errors import KpiSourceBindingError, KpiSourceSchemaError, KpiSourcesError
from ada.kpis.sources.loaded import (
    KpiSourceLoadFailure,
    LoadedKpiSource,
    LoadedKpiSources,
)
from ada.kpis.sources.reader import SourceDatasetReader
from ada.kpis.sources.shifts import MineShiftResolver


class KpiSourceLoader:
    def __init__(
        self,
        *,
        reader: SourceDatasetReader,
        registry: KpiSourceRegistry,
        shift_resolver: MineShiftResolver | None = None,
    ) -> None:
        if not isinstance(reader, SourceDatasetReader):
            raise TypeError('reader must implement SourceDatasetReader')
        if not isinstance(registry, KpiSourceRegistry):
            raise TypeError('registry must be KpiSourceRegistry')
        if shift_resolver is not None and not isinstance(shift_resolver, MineShiftResolver):
            raise TypeError('shift_resolver must be MineShiftResolver or None')
        self._reader = reader
        self._registry = registry
        self._shift_resolver = shift_resolver or MineShiftResolver()

    def load(self, *, plan: KpiLoadPlan, watermark: KpiWatermark) -> LoadedKpiSources:
        if not isinstance(plan, KpiLoadPlan):
            raise TypeError('plan must be KpiLoadPlan')
        if not isinstance(watermark, KpiWatermark):
            raise TypeError('watermark must be KpiWatermark')
        loaded: dict[KpiSource, LoadedKpiSource] = {}
        failures: dict[KpiSource, KpiSourceLoadFailure] = {}
        for source_plan in plan.sources:
            try:
                binding = self._registry.get(source_plan.source)
                loaded[source_plan.source] = self._load_source(
                    plan=source_plan,
                    binding=binding,
                    watermark=watermark,
                )
            except KpiSourcesError as error:
                failures[source_plan.source] = KpiSourceLoadFailure(
                    source=source_plan.source,
                    message=str(error),
                )
        return LoadedKpiSources(
            watermark=watermark,
            plan=plan,
            registry=self._registry,
            loaded=loaded,
            failures=failures,
            shift_resolver=self._shift_resolver,
        )

    def _load_source(
        self,
        *,
        plan: KpiSourceLoadPlan,
        binding: KpiSourceBinding,
        watermark: KpiWatermark,
    ) -> LoadedKpiSource:
        snapshot = (
            self._load_snapshot(binding=binding, columns=plan.snapshot_columns)
            if plan.snapshot_columns
            else None
        )
        time = (
            self._load_time(
                binding=binding,
                columns=plan.time_columns,
                watermark=watermark,
                plan=plan,
            )
            if plan.time_columns
            else None
        )
        shift = (
            self._load_shift(
                binding=binding,
                columns=plan.shift_columns,
                watermark=watermark,
                plan=plan,
            )
            if plan.shift_columns
            else None
        )
        return LoadedKpiSource(
            source=plan.source,
            snapshot_frame=snapshot,
            time_frame=time,
            shift_frame=shift,
        )

    def _load_snapshot(
        self,
        *,
        binding: KpiSourceBinding,
        columns: tuple[str, ...],
    ) -> pd.DataFrame:
        materialization = binding.snapshot_materialization
        if materialization is None:
            raise KpiSourceBindingError(
                f'{binding.source.value}: source does not support snapshot reads'
            )
        read_columns = _with_technical(columns, binding.timestamp_column)
        target = binding.definition.resolve_target(materialization=materialization)
        frame = self._reader.read_frame(
            definition=binding.definition,
            target=target,
            columns=read_columns,
        )
        output = _empty_or_copy(frame, read_columns)
        if binding.timestamp_column is not None:
            output = _normalize_timestamp(
                source=binding.source,
                frame=output,
                column=binding.timestamp_column,
            )
        return output

    def _load_time(
        self,
        *,
        binding: KpiSourceBinding,
        columns: tuple[str, ...],
        watermark: KpiWatermark,
        plan: KpiSourceLoadPlan,
    ) -> pd.DataFrame:
        materialization = binding.time_materialization
        timestamp_column = binding.timestamp_column
        granularity = binding.time_partition_granularity
        if materialization is None or timestamp_column is None or granularity is None:
            raise KpiSourceBindingError(
                f'{binding.source.value}: source does not support time-window reads'
            )
        assert plan.time_window is not None
        start = watermark.timestamp_utc - plan.time_window.to_timedelta()
        end = watermark.timestamp_utc
        read_columns = _with_technical(columns, timestamp_column)
        frames: list[pd.DataFrame] = []
        for partition in _time_partitions(start=start, end=end, granularity=granularity):
            target = binding.definition.resolve_target(
                materialization=materialization,
                partition=partition,
            )
            frame = self._reader.read_frame(
                definition=binding.definition,
                target=target,
                columns=read_columns,
                timestamp_column=timestamp_column,
                start_utc=start,
                end_utc=end,
            )
            if frame is not None:
                frames.append(frame)
        output = _concat_or_empty(frames, read_columns)
        return _normalize_timestamp(
            source=binding.source,
            frame=output,
            column=timestamp_column,
        )

    def _load_shift(
        self,
        *,
        binding: KpiSourceBinding,
        columns: tuple[str, ...],
        watermark: KpiWatermark,
        plan: KpiSourceLoadPlan,
    ) -> pd.DataFrame:
        materialization = binding.shift_materialization
        shift_column = binding.shift_column
        if materialization is None or shift_column is None:
            raise KpiSourceBindingError(
                f'{binding.source.value}: source does not support shift reads'
            )
        turns = {
            item.shift_id: item
            for selection in plan.shifts
            for item in self._shift_resolver.resolve(selection=selection, watermark=watermark)
        }
        ordered_turns = tuple(sorted(turns.values(), key=lambda item: item.shift_start_utc))
        read_columns = _with_technical(columns, shift_column)
        frames: list[pd.DataFrame] = []
        for turn in ordered_turns:
            target = binding.definition.resolve_target(
                materialization=materialization,
                partition=turn.partition,
            )
            frame = self._reader.read_frame(
                definition=binding.definition,
                target=target,
                columns=read_columns,
            )
            if frame is None:
                continue
            values = pd.to_numeric(frame[shift_column], errors='coerce')
            if values.isna().any():
                raise KpiSourceSchemaError(
                    f'{binding.source.value}: shift partition contains invalid shift_id values'
                )
            if values.ne(turn.shift_id).any():
                raise KpiSourceSchemaError(
                    f'{binding.source.value}: shift partition contains unexpected shift_id values'
                )
            frames.append(frame)
        return _concat_or_empty(frames, read_columns)


def _normalize_timestamp(
    *,
    source: KpiSource,
    frame: pd.DataFrame,
    column: str,
) -> pd.DataFrame:
    if column not in frame.columns:
        raise KpiSourceSchemaError(f'{source.value}: timestamp column is missing: {column}')
    if frame.empty:
        return frame.reset_index(drop=True)
    original = frame[column]
    parsed = pd.to_datetime(original, utc=True, errors='coerce')
    if parsed.isna().any():
        raise KpiSourceSchemaError(f'{source.value}: timestamp column contains invalid values')
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


def _with_technical(columns: tuple[str, ...], technical: str | None) -> tuple[str, ...]:
    if technical is None or technical in columns:
        return columns
    return (*columns, technical)


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
