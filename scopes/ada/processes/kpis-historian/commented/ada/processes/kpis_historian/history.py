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
_ERROR_HISTORY_DEFINITION = DatasetDefinition(
    key=DatasetKey(namespace=('kpis',), name='error-history'),
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
        pa.field('status', pa.string(), nullable=False),
        pa.field('value', pa.string(), nullable=True),
        pa.field('parsed_value', pa.string(), nullable=True),
    )
)
_ERROR_HISTORY_SCHEMA = pa.schema(
    (
        pa.field('timestamp_utc', pa.timestamp('us', tz='UTC'), nullable=False),
        pa.field('key', pa.string(), nullable=False),
        pa.field('error', pa.string(), nullable=False),
    )
)
_KEY_COLUMNS = ('timestamp_utc', 'key')
_ORDER_COLUMNS = ('timestamp_utc', 'key')


@dataclass(frozen=True, slots=True)
class KpiHistoryWriteResult:
    evaluation_count: int
    history_row_count: int
    error_row_count: int
    history_publication_count: int
    error_publication_count: int
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
        history_records: list[dict[str, object]] = []
        error_records: list[dict[str, object]] = []
        previous: KpiWatermark | None = None
        evaluation_count = 0
        history_row_count = 0
        error_row_count = 0
        history_publication_count = 0
        error_publication_count = 0

        for evaluation in iterator:
            _check_cancelled(check_cancelled)
            if not isinstance(evaluation, KpiEvaluation):
                raise TypeError('evaluations must contain KpiEvaluation values')
            if previous is not None and evaluation.watermark <= previous:
                raise KpiHistorianHistoryError('KPI evaluations must be strictly ordered')

            evaluation_day = evaluation.watermark.timestamp_utc.date()
            if current_day is not None and evaluation_day != current_day:
                history_publications, error_publications = self._flush(
                    day=current_day,
                    history_records=history_records,
                    error_records=error_records,
                )
                history_publication_count += history_publications
                error_publication_count += error_publications
                history_records = []
                error_records = []
            current_day = evaluation_day

            historical = evaluation.historical_results
            errors = evaluation.error_results
            history_records.extend(
                _history_record(evaluation=evaluation, result=result) for result in historical
            )
            error_records.extend(
                _error_record(evaluation=evaluation, result=result) for result in errors
            )
            history_row_count += len(historical)
            error_row_count += len(errors)
            evaluation_count += 1
            previous = evaluation.watermark

        if current_day is not None:
            _check_cancelled(check_cancelled)
            history_publications, error_publications = self._flush(
                day=current_day,
                history_records=history_records,
                error_records=error_records,
            )
            history_publication_count += history_publications
            error_publication_count += error_publications

        return KpiHistoryWriteResult(
            evaluation_count=evaluation_count,
            history_row_count=history_row_count,
            error_row_count=error_row_count,
            history_publication_count=history_publication_count,
            error_publication_count=error_publication_count,
            last_watermark=previous,
        )

    def _flush(
        self,
        *,
        day: date,
        history_records: list[dict[str, object]],
        error_records: list[dict[str, object]],
    ) -> tuple[int, int]:
        partition = {
            'year': f'{day.year:04d}',
            'month': f'{day.month:02d}',
            'day': f'{day.day:02d}',
        }
        history_publications = self._merge_records(
            definition=_HISTORY_DEFINITION,
            schema=_HISTORY_SCHEMA,
            partition=partition,
            records=history_records,
        )
        error_publications = self._merge_records(
            definition=_ERROR_HISTORY_DEFINITION,
            schema=_ERROR_HISTORY_SCHEMA,
            partition=partition,
            records=error_records,
        )
        return history_publications, error_publications

    def _merge_records(
        self,
        *,
        definition: DatasetDefinition,
        schema: pa.Schema,
        partition: dict[str, str],
        records: list[dict[str, object]],
    ) -> int:
        if not records:
            return 0
        target = definition.resolve_target(materialization='daily', partition=partition)
        table = pa.Table.from_pylist(records, schema=schema)
        self._runtime.merge(
            definition=definition,
            target=target,
            data=table,
            key_columns=_KEY_COLUMNS,
            order_by=_ORDER_COLUMNS,
        )
        return 1


def history_definition() -> DatasetDefinition:
    return _HISTORY_DEFINITION


def error_history_definition() -> DatasetDefinition:
    return _ERROR_HISTORY_DEFINITION


def history_schema() -> pa.Schema:
    return _HISTORY_SCHEMA


def error_history_schema() -> pa.Schema:
    return _ERROR_HISTORY_SCHEMA


def _history_record(*, evaluation: KpiEvaluation, result: KpiResult) -> dict[str, object]:
    return {
        'timestamp_utc': evaluation.watermark.timestamp_utc,
        'key': result.key,
        'status': result.status.value,
        'value': _json_value(result.value),
        'parsed_value': _json_value(result.parsed_value),
    }


def _error_record(*, evaluation: KpiEvaluation, result: KpiResult) -> dict[str, object]:
    if result.status is not KpiStatus.ERROR or result.error is None:
        raise KpiHistorianHistoryError(f'{result.key}: error history requires an ERROR result')
    return {
        'timestamp_utc': evaluation.watermark.timestamp_utc,
        'key': result.key,
        'error': result.error,
    }


def _json_value(value: object) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(',', ':'),
        )
    except (TypeError, ValueError) as error:
        raise KpiHistorianHistoryError('KPI history value is not valid JSON') from error


def _check_cancelled(check_cancelled: Callable[[], None] | None) -> None:
    if check_cancelled is not None:
        check_cancelled()
