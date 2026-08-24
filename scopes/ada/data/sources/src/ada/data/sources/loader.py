from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import pyarrow as pa

from ada.data.core import DataColumn, DataColumnType, DataSourceView, normalize_utc_second
from ada.data.planner import DataLoadPlan, DataSourceViewLoadPlan
from ada.data.sources.bindings import (
    DataPartitionBinding,
    DataSourceBinding,
    DataSourceRegistry,
    TimePartitionGranularity,
)
from ada.data.sources.errors import DataSourceSchemaError, DataSourcesError
from ada.data.sources.loaded import DataSourceLoadFailure, LoadedDataSources, LoadedDataSourceView
from ada.data.sources.operational import OperationalWindowResolver
from ada.data.sources.reader import SourceDatasetReader
from ada.data.sources.shifts import MineShiftResolver


class DataSourceLoader:
    def __init__(
        self,
        *,
        reader: SourceDatasetReader,
        registry: DataSourceRegistry,
        shift_resolver: MineShiftResolver | None = None,
        operational_resolver: OperationalWindowResolver | None = None,
    ) -> None:
        if not isinstance(reader, SourceDatasetReader):
            raise TypeError('reader must implement SourceDatasetReader')
        if not isinstance(registry, DataSourceRegistry):
            raise TypeError('registry must be DataSourceRegistry')
        if shift_resolver is not None and not isinstance(shift_resolver, MineShiftResolver):
            raise TypeError('shift_resolver must be MineShiftResolver or None')
        if operational_resolver is not None and not isinstance(
            operational_resolver, OperationalWindowResolver
        ):
            raise TypeError('operational_resolver must be OperationalWindowResolver or None')
        self._reader = reader
        self._registry = registry
        self._shift_resolver = shift_resolver or MineShiftResolver()
        self._operational_resolver = operational_resolver or OperationalWindowResolver()

    def load(self, *, plan: DataLoadPlan, as_of: datetime) -> LoadedDataSources:
        if not isinstance(plan, DataLoadPlan):
            raise TypeError('plan must be DataLoadPlan')
        normalized_as_of = normalize_utc_second(as_of, field_name='as_of')
        loaded: dict[DataSourceView, LoadedDataSourceView] = {}
        failures: dict[DataSourceView, DataSourceLoadFailure] = {}
        for view_plan in plan.views:
            try:
                binding, partition_binding = self._registry.get_view(view_plan.view)
                loaded[view_plan.view] = LoadedDataSourceView(
                    view=view_plan.view,
                    frame=self._load_view(
                        plan=view_plan,
                        binding=binding,
                        partition_binding=partition_binding,
                        as_of=normalized_as_of,
                    ),
                )
            except DataSourcesError as error:
                failures[view_plan.view] = DataSourceLoadFailure(
                    view=view_plan.view,
                    message=str(error),
                )
        return LoadedDataSources(
            as_of=normalized_as_of,
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
        plan: DataSourceViewLoadPlan,
        binding: DataSourceBinding,
        partition_binding: DataPartitionBinding,
        as_of: datetime,
    ) -> pd.DataFrame:
        projection_schema = _projection_schema(plan=plan, binding=partition_binding)
        if partition_binding.shift_column is not None:
            frame = self._load_shift_view(
                plan=plan,
                binding=binding,
                partition_binding=partition_binding,
                projection_schema=projection_schema,
                as_of=as_of,
            )
        elif partition_binding.time_partition_granularity is not None:
            frame = self._load_time_view(
                plan=plan,
                binding=binding,
                partition_binding=partition_binding,
                projection_schema=projection_schema,
                as_of=as_of,
            )
        else:
            target = binding.definition.resolve_target(
                materialization=partition_binding.materialization
            )
            raw = self._reader.read_frame(
                definition=binding.definition,
                target=target,
                projection_schema=projection_schema,
            )
            frame = _empty_or_copy(raw, projection_schema)
        _validate_technical_columns(
            frame,
            source=plan.source.value,
            timestamp_column=partition_binding.timestamp_column,
            shift_column=partition_binding.shift_column,
        )
        return frame

    def _load_time_view(
        self,
        *,
        plan: DataSourceViewLoadPlan,
        binding: DataSourceBinding,
        partition_binding: DataPartitionBinding,
        projection_schema: pa.Schema,
        as_of: datetime,
    ) -> pd.DataFrame:
        start_utc, end_utc = self._load_bounds(plan=plan, as_of=as_of)
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
                projection_schema=projection_schema,
                timestamp_column=partition_binding.timestamp_column,
                start_utc=start_utc,
                end_utc=end_utc,
            )
            if frame is not None:
                frames.append(frame)
        combined = _concat_or_empty(frames, projection_schema)
        return _sort_time(
            combined,
            source=plan.source.value,
            column=partition_binding.timestamp_column,
        )

    def _load_shift_view(
        self,
        *,
        plan: DataSourceViewLoadPlan,
        binding: DataSourceBinding,
        partition_binding: DataPartitionBinding,
        projection_schema: pa.Schema,
        as_of: datetime,
    ) -> pd.DataFrame:
        turns = []
        seen_shift_ids: set[int] = set()
        for selection in plan.shifts:
            for turn in self._shift_resolver.resolve(selection=selection, as_of=as_of):
                if turn.shift_id not in seen_shift_ids:
                    turns.append(turn)
                    seen_shift_ids.add(turn.shift_id)
        frames: list[pd.DataFrame] = []
        for turn in turns:
            target = binding.definition.resolve_target(
                materialization=partition_binding.materialization,
                partition=turn.partition,
            )
            frame = self._reader.read_frame(
                definition=binding.definition,
                target=target,
                projection_schema=projection_schema,
            )
            if frame is not None:
                frames.append(frame)
        return _concat_or_empty(frames, projection_schema)

    def _load_bounds(
        self,
        *,
        plan: DataSourceViewLoadPlan,
        as_of: datetime,
    ) -> tuple[datetime, datetime]:
        starts = [window.start_from(as_of) for window in plan.time_windows]
        ends = [as_of for _ in plan.time_windows]
        for scope in plan.operational_scopes:
            window = self._operational_resolver.resolve(scope=scope, as_of=as_of)
            starts.append(window.start_utc)
            ends.append(window.end_utc)
        if not starts:
            raise DataSourceSchemaError(
                f'{plan.source.value}/{plan.partition.value}: time partition requires a selector'
            )
        return min(starts), max(ends)


def _projection_schema(*, plan: DataSourceViewLoadPlan, binding: DataPartitionBinding) -> pa.Schema:
    columns = list(plan.columns)
    _append_technical_column(columns, binding.timestamp_column, DataColumnType.DATETIME)
    _append_technical_column(columns, binding.shift_column, DataColumnType.INTEGER)
    return pa.schema(
        [pa.field(column.name, _arrow_type(column.data_type), nullable=True) for column in columns]
    )


def _append_technical_column(
    columns: list[DataColumn],
    name: str | None,
    data_type: DataColumnType,
) -> None:
    if name is None:
        return
    existing = next((column for column in columns if column.name == name), None)
    if existing is None:
        columns.append(DataColumn(name, data_type))
        return
    if existing.data_type is not data_type:
        raise DataSourceSchemaError(
            f'{name}: projected data type conflicts with required technical type: '
            f'{existing.data_type.value} != {data_type.value}'
        )


def _arrow_type(data_type: DataColumnType) -> pa.DataType:
    if data_type is DataColumnType.TEXT:
        return pa.string()
    if data_type is DataColumnType.INTEGER:
        return pa.int64()
    if data_type is DataColumnType.FLOAT:
        return pa.float64()
    if data_type is DataColumnType.BOOLEAN:
        return pa.bool_()
    if data_type is DataColumnType.DATE:
        return pa.date32()
    return pa.timestamp('us', tz='UTC')


def _sort_time(frame: pd.DataFrame, *, source: str, column: str | None) -> pd.DataFrame:
    if column is None:
        raise DataSourceSchemaError(f'{source}: partitioned time view has no timestamp column')
    if column not in frame.columns:
        raise DataSourceSchemaError(f'{source}: timestamp column is missing: {column}')
    if frame.empty:
        return frame.reset_index(drop=True)
    parsed = pd.to_datetime(frame[column], utc=True, errors='coerce')
    if parsed.isna().any():
        raise DataSourceSchemaError(f'{source}: timestamp column contains invalid values')
    output = frame.copy(deep=False)
    output[column] = parsed
    return output.sort_values(column, kind='stable').reset_index(drop=True)


def _validate_technical_columns(
    frame: pd.DataFrame,
    *,
    source: str,
    timestamp_column: str | None,
    shift_column: str | None,
) -> None:
    if timestamp_column is not None:
        if timestamp_column not in frame.columns:
            raise DataSourceSchemaError(
                f'{source}: timestamp column is missing: {timestamp_column}'
            )
        if not frame.empty:
            timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors='coerce')
            if timestamps.isna().any():
                raise DataSourceSchemaError(f'{source}: timestamp column contains invalid values')
    if shift_column is not None:
        if shift_column not in frame.columns:
            raise DataSourceSchemaError(f'{source}: shift column is missing: {shift_column}')
        if not frame.empty:
            values = pd.to_numeric(frame[shift_column], errors='coerce')
            if values.isna().any():
                raise DataSourceSchemaError(f'{source}: shift column contains invalid values')


def _empty_or_copy(frame: pd.DataFrame | None, schema: pa.Schema) -> pd.DataFrame:
    if frame is None:
        return schema.empty_table().to_pandas()
    missing = tuple(column for column in schema.names if column not in frame.columns)
    if missing:
        raise DataSourceSchemaError(f'loaded dataframe is missing columns: {missing}')
    return frame.loc[:, schema.names].copy(deep=False).reset_index(drop=True)


def _concat_or_empty(frames: list[pd.DataFrame], schema: pa.Schema) -> pd.DataFrame:
    if not frames:
        return schema.empty_table().to_pandas()
    for frame in frames:
        missing = tuple(column for column in schema.names if column not in frame.columns)
        if missing:
            raise DataSourceSchemaError(f'loaded dataframe is missing columns: {missing}')
    return pd.concat(
        (frame.loc[:, schema.names] for frame in frames),
        ignore_index=True,
        sort=False,
    )


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
