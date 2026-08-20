# Materializa en Parquet únicamente observaciones KPI marcadas para historia.
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date

import pyarrow as pa

from ada.kpis.core import KpiEvaluation, KpiResult, KpiStatus, KpiWatermark
from ada.processes.kpis_historian.errors import KpiHistorianHistoryError
from atlanticus.datasets.layouts import SingleArtifactLayout
from atlanticus.datasets.models import DatasetDefinition, DatasetKey, MaterializationDefinition
from atlanticus.datasets.runtime import DatasetRuntime

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
_HISTORY_SCHEMA = pa.schema(
    (
        pa.field('timestamp_utc', pa.timestamp('us', tz='UTC'), nullable=False),
        pa.field('key', pa.string(), nullable=False),
        pa.field('area', pa.string(), nullable=False),
        pa.field('status', pa.string(), nullable=False),
        pa.field('value_kind', pa.string(), nullable=False),
        pa.field('value_json', pa.string(), nullable=True),
    )
)
_HISTORY_KEY_COLUMNS = ('timestamp_utc', 'key')
_HISTORY_ORDER_COLUMNS = ('timestamp_utc', 'key')


@dataclass(frozen=True, slots=True)
class KpiHistoryWriteResult:
    evaluation_count: int
    row_count: int
    publication_count: int
    last_watermark: KpiWatermark | None


class KpiHistoryWriter:
    def __init__(self, *, runtime: DatasetRuntime) -> None:
        if not isinstance(runtime, DatasetRuntime):
            raise TypeError('runtime must be DatasetRuntime')
        self._runtime = runtime

    def write(
        self,
        *,
        evaluations: Iterable[KpiEvaluation],
        check_cancelled: Callable[[], None] | None = None,
    ) -> KpiHistoryWriteResult:
        if isinstance(evaluations, KpiEvaluation | str | bytes):
            raise TypeError('evaluations must be an iterable of KpiEvaluation values')
        if check_cancelled is not None and not callable(check_cancelled):
            raise TypeError('check_cancelled must be callable or None')
        try:
            iterator = iter(evaluations)
        except TypeError as error:
            raise TypeError('evaluations must be an iterable of KpiEvaluation values') from error

        current_day: date | None = None
        records: list[dict[str, object]] = []
        previous: KpiWatermark | None = None
        evaluation_count = 0
        row_count = 0
        publication_count = 0

        for evaluation in iterator:
            _check_cancelled(check_cancelled)
            if not isinstance(evaluation, KpiEvaluation):
                raise TypeError('evaluations must contain KpiEvaluation values')
            if previous is not None and evaluation.watermark <= previous:
                raise KpiHistorianHistoryError('KPI evaluations must be strictly ordered')

            evaluation_day = evaluation.watermark.timestamp_utc.date()
            if current_day is not None and evaluation_day != current_day:
                publication_count += self._flush(day=current_day, records=records)
                records = []
            current_day = evaluation_day

            historical = evaluation.historical_results
            records.extend(
                _history_record(evaluation=evaluation, result=result) for result in historical
            )
            row_count += len(historical)
            evaluation_count += 1
            previous = evaluation.watermark

        if current_day is not None:
            _check_cancelled(check_cancelled)
            publication_count += self._flush(day=current_day, records=records)

        return KpiHistoryWriteResult(
            evaluation_count=evaluation_count,
            row_count=row_count,
            publication_count=publication_count,
            last_watermark=previous,
        )

    def _flush(self, *, day: date, records: list[dict[str, object]]) -> int:
        if not records:
            return 0
        target = _HISTORY_DEFINITION.resolve_target(
            materialization='daily',
            partition={
                'year': f'{day.year:04d}',
                'month': f'{day.month:02d}',
                'day': f'{day.day:02d}',
            },
        )
        table = pa.Table.from_pylist(records, schema=_HISTORY_SCHEMA)
        self._runtime.merge(
            definition=_HISTORY_DEFINITION,
            target=target,
            data=table,
            key_columns=_HISTORY_KEY_COLUMNS,
            order_by=_HISTORY_ORDER_COLUMNS,
        )
        return 1


def history_definition() -> DatasetDefinition:
    return _HISTORY_DEFINITION


def history_schema() -> pa.Schema:
    return _HISTORY_SCHEMA


def _history_record(*, evaluation: KpiEvaluation, result: KpiResult) -> dict[str, object]:
    return {
        'timestamp_utc': evaluation.watermark.timestamp_utc,
        'key': result.key,
        'area': result.area.value,
        'status': result.status.value,
        'value_kind': result.value_kind.value,
        'value_json': _value_json(result),
    }


def _value_json(result: KpiResult) -> str | None:
    if result.status is not KpiStatus.OK:
        return None
    try:
        return json.dumps(
            result.value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(',', ':'),
        )
    except (TypeError, ValueError) as error:
        raise KpiHistorianHistoryError(
            f'{result.key}: KPI history value is not valid JSON'
        ) from error


def _check_cancelled(check_cancelled: Callable[[], None] | None) -> None:
    if check_cancelled is not None:
        check_cancelled()
