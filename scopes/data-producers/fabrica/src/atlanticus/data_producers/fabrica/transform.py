from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd
import pyarrow as pa

from atlanticus.data_producers.fabrica.contracts import FabricaValueKind
from atlanticus.data_producers.fabrica.models import (
    FabricaKpiStreamDefinition,
    FabricaPlanStreamDefinition,
    FabricaStreamDefinition,
)

_SOURCE_TIMESTAMP = 'timestamp'
_SOURCE_ID = 'id_kpi'
_SOURCE_VALUE = 'valor'
_SOURCE_LEVEL = 'nivel'
_SOURCE_EXECUTION = 'timestamp_ejecucion'
_SOURCE_PARTITION = 'particion'


@dataclass(frozen=True, slots=True)
class FabricaTransformResult:
    frames: dict[object, pd.DataFrame]
    unknown_source_values: tuple[str, ...]
    source_row_count: int
    present_metric_keys: tuple[str, ...]
    missing_metric_keys: tuple[str, ...]
    missing_metric_keys_by_output: tuple[tuple[str, tuple[str, ...]], ...]
    metric_requests_expected: int
    metric_requests_present: int

    @property
    def metrics_expected(self) -> int:
        return self.metric_requests_expected

    @property
    def metrics_present(self) -> int:
        return self.metric_requests_present

    @property
    def metrics_missing(self) -> int:
        return self.metric_requests_expected - self.metric_requests_present


def build_partition_frames(
    *,
    table: pa.Table,
    definition: FabricaStreamDefinition,
) -> FabricaTransformResult:
    dataframe = table.to_pandas()
    dataframe[_SOURCE_ID] = dataframe[_SOURCE_ID].astype('string').str.strip().str.upper()
    dataframe[_SOURCE_LEVEL] = dataframe[_SOURCE_LEVEL].astype('string').str.strip().str.upper()
    outputs = _output_specs(definition)
    metric_ids = {_metric_source_id(metric) for _, _, _, metrics in outputs for metric in metrics}
    dataframe = dataframe[dataframe[_SOURCE_ID].isin(metric_ids)].copy()
    unknown_levels = _unknown_source_levels(dataframe=dataframe, definition=definition)
    allowed_pairs = {
        (_metric_source_id(metric), source_level)
        for _, _, source_level, metrics in outputs
        for metric in metrics
    }
    source_pairs = pd.MultiIndex.from_frame(dataframe[[_SOURCE_ID, _SOURCE_LEVEL]])
    dataframe = dataframe.loc[source_pairs.isin(allowed_pairs)].copy()
    source_rows = len(dataframe)
    dataframe[_SOURCE_TIMESTAMP] = _to_datetime_utc(dataframe[_SOURCE_TIMESTAMP])
    dataframe[_SOURCE_EXECUTION] = _to_datetime_utc(dataframe[_SOURCE_EXECUTION])
    dataframe[_SOURCE_PARTITION] = pd.to_numeric(
        dataframe[_SOURCE_PARTITION].map(_unwrap),
        errors='coerce',
    )
    dataframe['_source_order'] = range(len(dataframe))
    frames: dict[object, pd.DataFrame] = {}
    present_keys: set[str] = set()
    missing_by_output: list[tuple[str, tuple[str, ...]]] = []
    requests_present = 0
    for frame_key, output_key, source_level, metrics in outputs:
        subset = dataframe[dataframe[_SOURCE_LEVEL].eq(source_level)]
        frame, output_present = _wide_partition(dataframe=subset, metrics=metrics)
        frames[frame_key] = frame
        present_keys.update(output_present)
        requests_present += len(output_present)
        missing_by_output.append(
            (
                output_key,
                tuple(
                    metric.metric_key
                    for metric in metrics
                    if metric.metric_key not in output_present
                ),
            )
        )
    ordered_metrics = _ordered_unique_metrics(outputs)
    ordered_present = tuple(
        metric.metric_key for metric in ordered_metrics if metric.metric_key in present_keys
    )
    missing_unique = tuple(
        metric.metric_key
        for metric in ordered_metrics
        if any(metric.metric_key in missing for _, missing in missing_by_output)
    )
    return FabricaTransformResult(
        frames=frames,
        unknown_source_values=unknown_levels,
        source_row_count=source_rows,
        present_metric_keys=ordered_present,
        missing_metric_keys=missing_unique,
        missing_metric_keys_by_output=tuple(missing_by_output),
        metric_requests_expected=sum(len(metrics) for _, _, _, metrics in outputs),
        metric_requests_present=requests_present,
    )


def merge_partition_frame(
    *,
    current: pd.DataFrame | None,
    incoming: pd.DataFrame,
    metrics: Iterable[object],
) -> pd.DataFrame:
    metric_values = tuple(metrics)
    expected = ('timestamp', *(metric.metric_key for metric in metric_values))
    new = _normalize_wide(dataframe=incoming, metrics=metric_values)
    if current is None or current.empty:
        return new.loc[:, list(expected)]
    old = _normalize_wide(dataframe=current, metrics=metric_values)
    old = old.set_index('timestamp')
    new = new.set_index('timestamp')
    index = old.index.union(new.index).sort_values()
    output = pd.DataFrame(index=index)
    for metric in metric_values:
        old_values = old[metric.metric_key].reindex(index)
        new_values = new[metric.metric_key].reindex(index)
        output[metric.metric_key] = new_values.combine_first(old_values)
    output = output.reset_index(names='timestamp')
    return _normalize_wide(dataframe=output, metrics=metric_values).loc[:, list(expected)]


def _output_specs(
    definition: FabricaStreamDefinition,
) -> tuple[tuple[object, str, str, tuple[object, ...]], ...]:
    if isinstance(definition, FabricaPlanStreamDefinition):
        return tuple(
            (
                partition.key,
                partition.key.value,
                partition.source_value.strip().upper(),
                tuple(
                    metric for metric in definition.metrics if partition.key in metric.partitions
                ),
            )
            for partition in definition.partitions
        )
    if isinstance(definition, FabricaKpiStreamDefinition):
        return tuple(
            (
                dataset.name,
                dataset.name,
                dataset.level.value,
                tuple(dataset.metrics),
            )
            for dataset in definition.datasets
        )
    raise TypeError(f'Unsupported Fabrica stream definition: {type(definition)!r}')


def _unknown_source_levels(
    *,
    dataframe: pd.DataFrame,
    definition: FabricaStreamDefinition,
) -> tuple[str, ...]:
    if isinstance(definition, FabricaKpiStreamDefinition):
        return ()
    known_levels = {partition.source_value.strip().upper() for partition in definition.partitions}
    return tuple(
        sorted(
            value
            for value in dataframe[_SOURCE_LEVEL].dropna().unique().tolist()
            if value not in known_levels
        )
    )


def _ordered_unique_metrics(
    outputs: tuple[tuple[object, str, str, tuple[object, ...]], ...],
) -> tuple[object, ...]:
    ordered: dict[str, object] = {}
    for _, _, _, metrics in outputs:
        for metric in metrics:
            ordered.setdefault(metric.metric_key, metric)
    return tuple(ordered.values())


def _metric_source_id(metric: object) -> str:
    value = metric.id_kpi
    return str(getattr(value, 'value', value)).strip().upper()


def _wide_partition(
    *,
    dataframe: pd.DataFrame,
    metrics: tuple[object, ...],
) -> tuple[pd.DataFrame, set[str]]:
    series: list[pd.Series] = []
    present: set[str] = set()
    for metric in metrics:
        values = dataframe[dataframe[_SOURCE_ID].eq(_metric_source_id(metric))].copy()
        if values.empty:
            continue
        values['_parsed_value'] = _parse_values(values[_SOURCE_VALUE], metric.value_kind)
        values = values.dropna(subset=[_SOURCE_TIMESTAMP, '_parsed_value'])
        if values.empty:
            continue
        values = values.sort_values(
            by=[_SOURCE_TIMESTAMP, _SOURCE_PARTITION, _SOURCE_EXECUTION, '_source_order'],
            na_position='first',
            kind='mergesort',
        ).drop_duplicates(subset=[_SOURCE_TIMESTAMP], keep='last')
        current = values.set_index(_SOURCE_TIMESTAMP)['_parsed_value']
        current.name = metric.metric_key
        series.append(current)
        present.add(metric.metric_key)
    if series:
        output = pd.concat(series, axis=1, join='outer').sort_index().reset_index()
        output = output.rename(columns={_SOURCE_TIMESTAMP: 'timestamp'})
    else:
        output = pd.DataFrame({'timestamp': pd.Series(dtype='datetime64[ns, UTC]')})
    for metric in metrics:
        if metric.metric_key not in output.columns:
            output[metric.metric_key] = _empty_series(metric.value_kind, len(output))
    return _normalize_wide(dataframe=output, metrics=metrics), present


def _normalize_wide(*, dataframe: pd.DataFrame, metrics: tuple[object, ...]) -> pd.DataFrame:
    output = dataframe.copy()
    if 'timestamp' not in output.columns:
        output['timestamp'] = pd.Series(dtype='datetime64[ns, UTC]')
    output['timestamp'] = _to_datetime_utc(output['timestamp'])
    output = output.dropna(subset=['timestamp']).drop_duplicates(subset=['timestamp'], keep='last')
    for metric in metrics:
        if metric.metric_key not in output.columns:
            output[metric.metric_key] = _empty_series(metric.value_kind, len(output))
        else:
            output[metric.metric_key] = _parse_values(output[metric.metric_key], metric.value_kind)
    return output.sort_values('timestamp').reset_index(drop=True)


def _parse_values(values: pd.Series, kind: FabricaValueKind) -> pd.Series:
    values = values.map(_unwrap)
    if kind is FabricaValueKind.NUMBER:
        return pd.to_numeric(values, errors='coerce').astype('Float64')
    if kind is FabricaValueKind.INTEGER:
        numeric = pd.to_numeric(values, errors='coerce')
        numeric = numeric.where(numeric.isna() | numeric.mod(1).eq(0))
        return numeric.astype('Int64')
    if kind is FabricaValueKind.TEXT:
        output = values.astype('string').str.strip()
        return output.mask(output.eq(''), pd.NA)
    if kind is FabricaValueKind.BOOLEAN:
        return values.map(_boolean_value).astype('boolean')
    if kind is FabricaValueKind.DATETIME:
        return _to_datetime_utc(values)
    raise ValueError(f'Unsupported Fabrica value kind: {kind!r}')


def _boolean_value(value: object) -> object:
    if pd.isna(value):
        return pd.NA
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
        return pd.NA
    normalized = str(value).strip().lower()
    return {'true': True, 'false': False, '1': True, '0': False}.get(normalized, pd.NA)


def _empty_series(kind: FabricaValueKind, length: int) -> pd.Series:
    if kind is FabricaValueKind.NUMBER:
        dtype = 'Float64'
    elif kind is FabricaValueKind.INTEGER:
        dtype = 'Int64'
    elif kind is FabricaValueKind.TEXT:
        dtype = 'string'
    elif kind is FabricaValueKind.BOOLEAN:
        dtype = 'boolean'
    else:
        dtype = 'datetime64[ns, UTC]'
    return pd.Series([pd.NA] * length, dtype=dtype)


def _to_datetime_utc(values: pd.Series) -> pd.Series:
    raw = values.map(_unwrap)
    numeric = pd.to_numeric(raw, errors='coerce')
    if numeric.notna().any():
        unit = _infer_epoch_unit(numeric)
        numeric_dates = pd.to_datetime(numeric, unit=unit, utc=True, errors='coerce')
        if numeric.notna().all():
            return numeric_dates
        direct = pd.to_datetime(raw.where(numeric.isna()), utc=True, errors='coerce')
        return numeric_dates.combine_first(direct)
    return pd.to_datetime(raw, utc=True, errors='coerce')


def _unwrap(value: object) -> object:
    if isinstance(value, dict) and 'value' in value:
        return value.get('value')
    if hasattr(value, 'as_py'):
        return value.as_py()
    return value


def _infer_epoch_unit(values: pd.Series) -> str:
    maximum = values.abs().max(skipna=True)
    if pd.isna(maximum):
        return 's'
    if maximum >= 1e17:
        return 'ns'
    if maximum >= 1e14:
        return 'us'
    if maximum >= 1e11:
        return 'ms'
    return 's'
