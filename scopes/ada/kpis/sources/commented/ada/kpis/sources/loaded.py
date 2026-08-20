# Recorta la carga física amplia hasta la vista exacta solicitada por cada KPI.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import pandas as pd

from ada.kpis.core import DataRuntimeContext, KpiSourceView, KpiWatermark, SourceRequirement
from ada.kpis.planner import KpiLoadPlan
from ada.kpis.sources.bindings import KpiSourceRegistry
from ada.kpis.sources.errors import KpiSourceSchemaError, KpiSourceUnavailableError
from ada.kpis.sources.frame import PandasRuntimeFrameContext
from ada.kpis.sources.operational import KpiOperationalWindowResolver
from ada.kpis.sources.shifts import MineShiftResolver


@dataclass(frozen=True, slots=True)
class KpiSourceLoadFailure:
    view: KpiSourceView
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.view, KpiSourceView):
            raise TypeError('view must be KpiSourceView')
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError('message must be non-empty text')
        object.__setattr__(self, 'message', self.message.strip())


@dataclass(frozen=True, slots=True)
class LoadedKpiSourceView:
    view: KpiSourceView
    frame: pd.DataFrame

    def __post_init__(self) -> None:
        if not isinstance(self.view, KpiSourceView):
            raise TypeError('view must be KpiSourceView')
        if not isinstance(self.frame, pd.DataFrame):
            raise TypeError('frame must be pandas.DataFrame')
        object.__setattr__(self, 'frame', self.frame.copy(deep=False).reset_index(drop=True))


@dataclass(frozen=True, slots=True)
class LoadedKpiSources:
    watermark: KpiWatermark
    plan: KpiLoadPlan
    registry: KpiSourceRegistry
    loaded: Mapping[KpiSourceView, LoadedKpiSourceView]
    failures: Mapping[KpiSourceView, KpiSourceLoadFailure]
    shift_resolver: MineShiftResolver = MineShiftResolver()
    operational_resolver: KpiOperationalWindowResolver = KpiOperationalWindowResolver()

    def __post_init__(self) -> None:
        if not isinstance(self.watermark, KpiWatermark):
            raise TypeError('watermark must be KpiWatermark')
        if not isinstance(self.plan, KpiLoadPlan):
            raise TypeError('plan must be KpiLoadPlan')
        if not isinstance(self.registry, KpiSourceRegistry):
            raise TypeError('registry must be KpiSourceRegistry')
        loaded = dict(self.loaded)
        failures = dict(self.failures)
        for view, item in loaded.items():
            if not isinstance(view, KpiSourceView) or not isinstance(item, LoadedKpiSourceView):
                raise TypeError('loaded must map KpiSourceView to LoadedKpiSourceView')
            if item.view != view:
                raise ValueError('loaded view key must match loaded view value')
        for view, failure in failures.items():
            if not isinstance(view, KpiSourceView) or not isinstance(failure, KpiSourceLoadFailure):
                raise TypeError('failures must map KpiSourceView to KpiSourceLoadFailure')
            if failure.view != view:
                raise ValueError('failure view key must match failure view value')
        overlap = set(loaded).intersection(failures)
        if overlap:
            raise ValueError('a source view cannot be loaded and failed at the same time')
        object.__setattr__(self, 'loaded', MappingProxyType(loaded))
        object.__setattr__(self, 'failures', MappingProxyType(failures))

    def context_for(self, kpi_key: str) -> DataRuntimeContext:
        requirements = self.plan.requirements_for(kpi_key)
        frames = {
            requirement.view: self._frame_for(requirement=requirement)
            for requirement in requirements
        }
        return DataRuntimeContext(frames=frames)

    def _frame_for(self, *, requirement: SourceRequirement) -> PandasRuntimeFrameContext:
        view = requirement.view
        failure = self.failures.get(view)
        if failure is not None:
            raise KpiSourceUnavailableError(
                view.source,
                f'{view.partition.value}: {failure.message}',
            )
        try:
            loaded = self.loaded[view]
        except KeyError as error:
            raise KpiSourceUnavailableError(
                view.source,
                f'{view.partition.value}: source view was not loaded',
            ) from error
        binding, partition_binding = self.registry.get_view(view)
        exact = loaded.frame
        if requirement.time_window is not None:
            exact = _slice_time(
                frame=exact,
                source=view.source.value,
                timestamp_column=partition_binding.timestamp_column,
                start_utc=requirement.time_window.start_from(self.watermark.timestamp_utc),
                end_utc=self.watermark.timestamp_utc,
            )
        elif requirement.operational_scope is not None:
            window = self.operational_resolver.resolve(
                scope=requirement.operational_scope,
                watermark=self.watermark,
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
                        watermark=self.watermark,
                    )
                ),
            )
        missing = tuple(column for column in requirement.columns if column not in exact.columns)
        if missing:
            raise KpiSourceSchemaError(
                f'{view.source.value}/{view.partition.value}: requested columns are missing: {missing}'
            )
        projected = exact.loc[:, list(requirement.columns)].copy(deep=False).reset_index(drop=True)
        return PandasRuntimeFrameContext(projected, requirement.columns)


def _slice_time(
    *,
    frame: pd.DataFrame,
    source: str,
    timestamp_column: str | None,
    start_utc,
    end_utc,
) -> pd.DataFrame:
    if timestamp_column is None:
        raise KpiSourceSchemaError(f'{source}: source view has no timestamp column')
    if timestamp_column not in frame.columns:
        raise KpiSourceSchemaError(f'{source}: timestamp column is missing: {timestamp_column}')
    timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors='coerce')
    if timestamps.isna().any():
        raise KpiSourceSchemaError(f'{source}: timestamp column contains invalid values')
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
        raise KpiSourceSchemaError(f'{source}: source view has no shift column')
    if shift_column not in frame.columns:
        raise KpiSourceSchemaError(f'{source}: shift column is missing: {shift_column}')
    values = pd.to_numeric(frame[shift_column], errors='coerce')
    return frame.loc[values.isin(shift_ids)].copy(deep=False)
