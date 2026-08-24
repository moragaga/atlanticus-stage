from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

import pandas as pd

from ada.data.core import DataRequirement, DataRuntimeContext, DataSourceView, normalize_utc_second
from ada.data.planner import DataLoadPlan
from ada.data.sources.bindings import DataSourceRegistry
from ada.data.sources.errors import DataSourceSchemaError, DataSourceUnavailableError
from ada.data.sources.frame import PandasRuntimeFrameContext
from ada.data.sources.operational import OperationalWindowResolver
from ada.data.sources.shifts import MineShiftResolver


@dataclass(frozen=True, slots=True)
class DataSourceLoadFailure:
    view: DataSourceView
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.view, DataSourceView):
            raise TypeError('view must be DataSourceView')
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError('message must be non-empty text')
        object.__setattr__(self, 'message', self.message.strip())


@dataclass(frozen=True, slots=True)
class LoadedDataSourceView:
    view: DataSourceView
    frame: pd.DataFrame

    def __post_init__(self) -> None:
        if not isinstance(self.view, DataSourceView):
            raise TypeError('view must be DataSourceView')
        if not isinstance(self.frame, pd.DataFrame):
            raise TypeError('frame must be pandas.DataFrame')
        object.__setattr__(self, 'frame', self.frame.copy(deep=False).reset_index(drop=True))


@dataclass(frozen=True, slots=True)
class LoadedDataSources:
    as_of: datetime
    plan: DataLoadPlan
    registry: DataSourceRegistry
    loaded: Mapping[DataSourceView, LoadedDataSourceView]
    failures: Mapping[DataSourceView, DataSourceLoadFailure]
    shift_resolver: MineShiftResolver = MineShiftResolver()
    operational_resolver: OperationalWindowResolver = OperationalWindowResolver()

    def __post_init__(self) -> None:
        as_of = normalize_utc_second(self.as_of, field_name='as_of')
        if not isinstance(self.plan, DataLoadPlan):
            raise TypeError('plan must be DataLoadPlan')
        if not isinstance(self.registry, DataSourceRegistry):
            raise TypeError('registry must be DataSourceRegistry')
        loaded = dict(self.loaded)
        failures = dict(self.failures)
        for view, item in loaded.items():
            if not isinstance(view, DataSourceView) or not isinstance(item, LoadedDataSourceView):
                raise TypeError('loaded must map DataSourceView to LoadedDataSourceView')
            if item.view != view:
                raise ValueError('loaded view key must match loaded view value')
        for view, failure in failures.items():
            if not isinstance(view, DataSourceView) or not isinstance(
                failure, DataSourceLoadFailure
            ):
                raise TypeError('failures must map DataSourceView to DataSourceLoadFailure')
            if failure.view != view:
                raise ValueError('failure view key must match failure view value')
        overlap = set(loaded).intersection(failures)
        if overlap:
            raise ValueError('a source view cannot be loaded and failed at the same time')
        object.__setattr__(self, 'as_of', as_of)
        object.__setattr__(self, 'loaded', MappingProxyType(loaded))
        object.__setattr__(self, 'failures', MappingProxyType(failures))

    def context_for(self, key: str) -> DataRuntimeContext:
        requirements = self.plan.requirements_for(key)
        frames = {
            requirement.view: self._frame_for(requirement=requirement)
            for requirement in requirements
        }
        return DataRuntimeContext(frames=frames)

    def _frame_for(self, *, requirement: DataRequirement) -> PandasRuntimeFrameContext:
        view = requirement.view
        failure = self.failures.get(view)
        if failure is not None:
            raise DataSourceUnavailableError(
                view.source,
                f'{view.partition.value}: {failure.message}',
            )
        try:
            loaded = self.loaded[view]
        except KeyError as error:
            raise DataSourceUnavailableError(
                view.source,
                f'{view.partition.value}: source view was not loaded',
            ) from error
        _, partition_binding = self.registry.get_view(view)
        exact = loaded.frame
        if requirement.time_window is not None:
            exact = _slice_time(
                frame=exact,
                source=view.source.value,
                timestamp_column=partition_binding.timestamp_column,
                start_utc=requirement.time_window.start_from(self.as_of),
                end_utc=self.as_of,
            )
        elif requirement.operational_scope is not None:
            window = self.operational_resolver.resolve(
                scope=requirement.operational_scope,
                as_of=self.as_of,
            )
            exact = _slice_time(
                frame=exact,
                source=view.source.value,
                timestamp_column=partition_binding.timestamp_column,
                start_utc=window.start_utc,
                end_utc=window.end_utc,
            )
        elif requirement.shift is not None:
            exact = _slice_shift(
                frame=exact,
                source=view.source.value,
                shift_column=partition_binding.shift_column,
                shift_ids=tuple(
                    item.shift_id
                    for item in self.shift_resolver.resolve(
                        selection=requirement.shift,
                        as_of=self.as_of,
                    )
                ),
            )
        column_names = requirement.column_names
        missing = tuple(column for column in column_names if column not in exact.columns)
        if missing:
            raise DataSourceSchemaError(
                f'{view.source.value}/{view.partition.value}: requested columns are missing: {missing}'
            )
        projected = exact.loc[:, list(column_names)].copy(deep=False).reset_index(drop=True)
        return PandasRuntimeFrameContext(projected, column_names)


def _slice_time(
    *,
    frame: pd.DataFrame,
    source: str,
    timestamp_column: str | None,
    start_utc: datetime,
    end_utc: datetime,
) -> pd.DataFrame:
    if timestamp_column is None:
        raise DataSourceSchemaError(f'{source}: source view has no timestamp column')
    if timestamp_column not in frame.columns:
        raise DataSourceSchemaError(f'{source}: timestamp column is missing: {timestamp_column}')
    timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors='coerce')
    if timestamps.isna().any():
        raise DataSourceSchemaError(f'{source}: timestamp column contains invalid values')
    return frame.loc[
        (timestamps >= pd.Timestamp(start_utc)) & (timestamps <= pd.Timestamp(end_utc))
    ].copy(deep=False)


def _slice_shift(
    *,
    frame: pd.DataFrame,
    source: str,
    shift_column: str | None,
    shift_ids: tuple[int, ...],
) -> pd.DataFrame:
    if shift_column is None:
        raise DataSourceSchemaError(f'{source}: source view has no shift column')
    if shift_column not in frame.columns:
        raise DataSourceSchemaError(f'{source}: shift column is missing: {shift_column}')
    values = pd.to_numeric(frame[shift_column], errors='coerce')
    return frame.loc[values.isin(shift_ids)].copy(deep=False)
