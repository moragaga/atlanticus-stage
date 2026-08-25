from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from ada.kpis.delivery import KpiTimeseriesPoint
from ada.processes.kpis_timeseries_delivery.errors import KpiTimeseriesDeliveryHistoryError
from atlanticus.datasets import DatasetDefinition, DatasetKey, MaterializationDefinition
from atlanticus.datasets.layouts import SingleArtifactLayout
from atlanticus.datasets.parquet import ColumnFilter, FilterOperator
from atlanticus.datasets.runtime import DatasetRuntime, DatasetRuntimeNotFoundError

_HISTORY_DEFINITION = DatasetDefinition(
    key=DatasetKey(namespace=('kpis',), name='history'),
    materializations=(
        MaterializationDefinition(
            name='daily',
            layout=SingleArtifactLayout(),
            partition_dimensions=('year', 'month', 'day'),
            route_segments=(),
        ),
    ),
)


class KpiTimeseriesHistoryRepository:
    def __init__(self, *, runtime: DatasetRuntime) -> None:
        if not isinstance(runtime, DatasetRuntime):
            raise TypeError('runtime must be DatasetRuntime')
        self._runtime = runtime

    def read_points(
        self,
        *,
        keys: tuple[str, ...],
        start_utc: datetime,
        end_utc: datetime,
        step_seconds: int,
    ) -> tuple[KpiTimeseriesPoint, ...]:
        normalized_keys = _keys(keys)
        timestamps = _timestamps(
            start_utc=start_utc,
            end_utc=end_utc,
            step_seconds=step_seconds,
        )
        if not normalized_keys or not timestamps:
            return ()
        filters = (
            ColumnFilter(column='key', operator=FilterOperator.IN, value=normalized_keys),
            ColumnFilter(column='timestamp_utc', operator=FilterOperator.IN, value=timestamps),
        )
        points: list[KpiTimeseriesPoint] = []
        for day in _days(timestamps[0].date(), timestamps[-1].date()):
            target = _HISTORY_DEFINITION.resolve_target(
                materialization='daily',
                partition={
                    'year': f'{day.year:04d}',
                    'month': f'{day.month:02d}',
                    'day': f'{day.day:02d}',
                },
            )
            try:
                result = self._runtime.scan_table(
                    definition=_HISTORY_DEFINITION,
                    targets=(target,),
                    columns=('timestamp_utc', 'key', 'value'),
                    filters=filters,
                )
            except DatasetRuntimeNotFoundError:
                continue
            for record in result.table.to_pylist():
                points.append(_point(record))
        return tuple(sorted(points, key=lambda item: (item.timestamp_utc, item.key)))


def _point(record: dict[str, object]) -> KpiTimeseriesPoint:
    timestamp = record.get('timestamp_utc')
    key = record.get('key')
    raw_value = record.get('value')
    if not isinstance(timestamp, datetime):
        raise KpiTimeseriesDeliveryHistoryError('KPI history timestamp_utc is invalid')
    if not isinstance(key, str) or not key:
        raise KpiTimeseriesDeliveryHistoryError('KPI history key is invalid')
    value = None if raw_value is None else _decode_value(raw_value)
    return KpiTimeseriesPoint(timestamp_utc=timestamp, key=key, value=value)


def _decode_value(value: object) -> object:
    if not isinstance(value, str):
        raise KpiTimeseriesDeliveryHistoryError('KPI history value is invalid')
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise KpiTimeseriesDeliveryHistoryError('KPI history value is invalid JSON') from error
    if decoded is None or isinstance(decoded, str | int | float) and not isinstance(decoded, bool):
        return decoded
    raise KpiTimeseriesDeliveryHistoryError(
        'KPI history timeseries value must be numeric, text, or null'
    )


def _keys(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError('keys must be a tuple')
    normalized = tuple(value for value in values if isinstance(value, str) and value)
    if len(normalized) != len(values):
        raise ValueError('keys must contain non-empty strings')
    return tuple(dict.fromkeys(normalized))


def _timestamps(
    *,
    start_utc: datetime,
    end_utc: datetime,
    step_seconds: int,
) -> tuple[datetime, ...]:
    if not isinstance(start_utc, datetime) or not isinstance(end_utc, datetime):
        raise TypeError('start_utc and end_utc must be datetime')
    if not isinstance(step_seconds, int) or isinstance(step_seconds, bool) or step_seconds <= 0:
        raise ValueError('step_seconds must be an integer greater than zero')
    duration = int((end_utc - start_utc).total_seconds())
    if duration < 0 or duration % step_seconds != 0:
        raise ValueError('timeseries window must be positive and divisible by step_seconds')
    step = timedelta(seconds=step_seconds)
    return tuple(start_utc + step * index for index in range(1, duration // step_seconds + 1))


def _days(start: date, end: date) -> tuple[date, ...]:
    count = (end - start).days
    return tuple(start + timedelta(days=index) for index in range(count + 1))
