# Espejo pedagógico: el código ejecutable es idéntico; los comentarios explican responsabilidades y fronteras.
# Aquí se cumple la frontera clave: la sobrelectura física se recorta al requirement exacto antes del resolver.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import pandas as pd

from ada.kpis.core import DataRuntimeContext, KpiSource, KpiWatermark, SourceRequirement
from ada.kpis.planner import KpiLoadPlan
from ada.kpis.sources.bindings import KpiSourceBinding, KpiSourceRegistry
from ada.kpis.sources.errors import KpiSourceSchemaError, KpiSourceUnavailableError
from ada.kpis.sources.frame import PandasRuntimeFrameContext
from ada.kpis.sources.shifts import MineShiftResolver


@dataclass(frozen=True, slots=True)
class KpiSourceLoadFailure:
    source: KpiSource
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, KpiSource):
            raise TypeError('source must be KpiSource')
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError('message must be non-empty text')
        object.__setattr__(self, 'message', self.message.strip())


@dataclass(frozen=True, slots=True)
class LoadedKpiSource:
    source: KpiSource
    snapshot_frame: pd.DataFrame | None = None
    time_frame: pd.DataFrame | None = None
    shift_frame: pd.DataFrame | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, KpiSource):
            raise TypeError('source must be KpiSource')
        for field_name in ('snapshot_frame', 'time_frame', 'shift_frame'):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, pd.DataFrame):
                raise TypeError(f'{field_name} must be pandas.DataFrame or None')
            if value is not None:
                object.__setattr__(self, field_name, value.copy(deep=False).reset_index(drop=True))


@dataclass(frozen=True, slots=True)
class LoadedKpiSources:
    watermark: KpiWatermark
    plan: KpiLoadPlan
    registry: KpiSourceRegistry
    loaded: Mapping[KpiSource, LoadedKpiSource]
    failures: Mapping[KpiSource, KpiSourceLoadFailure]
    shift_resolver: MineShiftResolver = MineShiftResolver()

    def __post_init__(self) -> None:
        if not isinstance(self.watermark, KpiWatermark):
            raise TypeError('watermark must be KpiWatermark')
        if not isinstance(self.plan, KpiLoadPlan):
            raise TypeError('plan must be KpiLoadPlan')
        if not isinstance(self.registry, KpiSourceRegistry):
            raise TypeError('registry must be KpiSourceRegistry')
        loaded = dict(self.loaded)
        failures = dict(self.failures)
        for source, item in loaded.items():
            if not isinstance(source, KpiSource) or not isinstance(item, LoadedKpiSource):
                raise TypeError('loaded must map KpiSource to LoadedKpiSource')
            if item.source is not source:
                raise ValueError('loaded source key must match loaded source value')
        for source, failure in failures.items():
            if not isinstance(source, KpiSource) or not isinstance(failure, KpiSourceLoadFailure):
                raise TypeError('failures must map KpiSource to KpiSourceLoadFailure')
            if failure.source is not source:
                raise ValueError('failure source key must match failure source value')
        overlap = set(loaded).intersection(failures)
        if overlap:
            raise ValueError('a source cannot be loaded and failed at the same time')
        object.__setattr__(self, 'loaded', MappingProxyType(loaded))
        object.__setattr__(self, 'failures', MappingProxyType(failures))

    def context_for(self, kpi_key: str) -> DataRuntimeContext:
        requirements = self.plan.requirements_for(kpi_key)
        frames = {
            source: self._frame_for(source=source, requirement=requirement)
            for source, requirement in requirements.items()
        }
        return DataRuntimeContext(frames=frames)

    def _frame_for(
        self,
        *,
        source: KpiSource,
        requirement: SourceRequirement,
    ) -> PandasRuntimeFrameContext:
        failure = self.failures.get(source)
        if failure is not None:
            raise KpiSourceUnavailableError(source, failure.message)
        try:
            loaded = self.loaded[source]
        except KeyError as error:
            raise KpiSourceUnavailableError(source, 'source was not loaded') from error
        binding = self.registry.get(source)
        if requirement.time_window is not None:
            frame = _require_frame(source, loaded.time_frame, 'time')
            exact = _slice_time(
                frame=frame,
                binding=binding,
                watermark=self.watermark,
                requirement=requirement,
            )
        elif requirement.shift is not None:
            frame = _require_frame(source, loaded.shift_frame, 'shift')
            exact = _slice_shift(
                frame=frame,
                binding=binding,
                shift_ids=tuple(
                    item.shift_id
                    for item in self.shift_resolver.resolve(
                        selection=requirement.shift,
                        watermark=self.watermark,
                    )
                ),
            )
        else:
            exact = _require_frame(source, loaded.snapshot_frame, 'snapshot').copy(deep=False)
        missing = tuple(column for column in requirement.columns if column not in exact.columns)
        if missing:
            raise KpiSourceSchemaError(
                f'{source.value}: requested columns are missing from loaded source: {missing}'
            )
        projected = exact.loc[:, list(requirement.columns)].copy(deep=False).reset_index(drop=True)
        return PandasRuntimeFrameContext(projected, requirement.columns)


def _slice_time(
    *,
    frame: pd.DataFrame,
    binding: KpiSourceBinding,
    watermark: KpiWatermark,
    requirement: SourceRequirement,
) -> pd.DataFrame:
    timestamp_column = binding.timestamp_column
    assert timestamp_column is not None
    if timestamp_column not in frame.columns:
        raise KpiSourceSchemaError(
            f'{binding.source.value}: timestamp column is missing: {timestamp_column}'
        )
    assert requirement.time_window is not None
    timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors='coerce')
    start = pd.Timestamp(watermark.timestamp_utc - requirement.time_window.to_timedelta())
    end = pd.Timestamp(watermark.timestamp_utc)
    return frame.loc[(timestamps >= start) & (timestamps <= end)].copy(deep=False)


def _slice_shift(
    *,
    frame: pd.DataFrame,
    binding: KpiSourceBinding,
    shift_ids: tuple[int, ...],
) -> pd.DataFrame:
    shift_column = binding.shift_column
    assert shift_column is not None
    if shift_column not in frame.columns:
        raise KpiSourceSchemaError(
            f'{binding.source.value}: shift column is missing: {shift_column}'
        )
    values = pd.to_numeric(frame[shift_column], errors='coerce')
    return frame.loc[values.isin(shift_ids)].copy(deep=False)


def _require_frame(source: KpiSource, value: pd.DataFrame | None, group: str) -> pd.DataFrame:
    if value is None:
        raise KpiSourceUnavailableError(source, f'{group} load group is unavailable')
    return value
