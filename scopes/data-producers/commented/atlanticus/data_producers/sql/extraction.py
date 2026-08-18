# Lee markers y datos SQL por batches, con retry y salida Arrow.
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import TypeVar

import pyarrow as pa

from atlanticus.connectivity.sql import (
    SqlBatch,
    SqlClient,
    SqlConnectionError,
    SqlTableChangeMarker,
    SqlTimeoutError,
)
from atlanticus.data_producers.sql.errors import SqlDataProducerReadError
from atlanticus.data_producers.sql.models import SqlLoadStrategy, SqlSourceDefinition, SqlSourcePlan
from atlanticus.data_producers.sql.settings import SqlRetryPolicy
from atlanticus.runtime import JobRuntimeContext

_T = TypeVar('_T')
_RETRYABLE_ERRORS = (SqlConnectionError, SqlTimeoutError)


class SqlDataProducerReader:
    def __init__(
        self,
        *,
        sql: SqlClient,
        retry_policy: SqlRetryPolicy | None = None,
        max_rows: int | None = None,
    ) -> None:
        if not isinstance(sql, SqlClient):
            raise TypeError('sql must be a SqlClient')
        resolved_max_rows = sql.settings.max_query_rows if max_rows is None else max_rows
        if (
            not isinstance(resolved_max_rows, int)
            or isinstance(resolved_max_rows, bool)
            or resolved_max_rows <= 0
        ):
            raise ValueError('max_rows must be an integer greater than zero')
        self._sql = sql
        self._retry_policy = retry_policy or SqlRetryPolicy()
        self._max_rows = resolved_max_rows

    def read_change_markers(
        self,
        definitions: Sequence[SqlSourceDefinition],
        *,
        context: JobRuntimeContext | None = None,
    ) -> dict[str, SqlTableChangeMarker]:
        normalized = tuple(definitions)
        if not normalized:
            return {}
        markers = self._run_with_retry(
            lambda: self._sql.table_change_markers(
                tuple(definition.source_table for definition in normalized)
            ),
            context=context,
        )
        by_table = {marker.source_table.lower(): marker for marker in markers}
        resolved: dict[str, SqlTableChangeMarker] = {}
        for definition in normalized:
            marker = by_table.get(definition.source_table.lower())
            if marker is None:
                raise SqlDataProducerReadError(
                    f'SQL change marker is missing for source table: {definition.source_table}'
                )
            resolved[definition.source_key] = marker
        return resolved

    def read_source(
        self,
        plan: SqlSourcePlan,
        *,
        context: JobRuntimeContext | None = None,
    ) -> pa.Table:
        if not isinstance(plan, SqlSourcePlan):
            raise TypeError('plan must be a SqlSourcePlan')
        return self._run_with_retry(
            lambda: self._read_source_once(plan, context=context),
            context=context,
        )

    def _read_source_once(
        self,
        plan: SqlSourcePlan,
        *,
        context: JobRuntimeContext | None = None,
    ) -> pa.Table:
        statement, parameters = build_select(plan)
        expected_columns = plan.definition.expected_output_columns
        tables: list[pa.Table] = []
        row_count = 0
        if context is not None:
            context.raise_if_cancelled()
        with self._sql.iter_batches(statement, parameters) as stream:
            for batch in stream:
                if context is not None:
                    context.raise_if_cancelled()
                table = _batch_to_table(batch, expected_columns=expected_columns)
                row_count += table.num_rows
                if row_count > self._max_rows:
                    source_key = plan.definition.source_key
                    raise SqlDataProducerReadError(
                        f'SQL source exceeded the configured row limit: {source_key}'
                    )
                tables.append(table)
        if context is not None:
            context.raise_if_cancelled()
        if not tables:
            return pa.table({name: pa.array([], type=pa.null()) for name in expected_columns})
        try:
            return pa.concat_tables(tables, promote_options='permissive')
        except (pa.ArrowException, TypeError, ValueError) as error:
            raise SqlDataProducerReadError(
                'SQL batches could not be combined as Arrow data'
            ) from error

    def _run_with_retry(
        self,
        operation: Callable[[], _T],
        *,
        context: JobRuntimeContext | None = None,
    ) -> _T:
        for attempt in range(1, self._retry_policy.attempts + 1):
            if context is not None:
                context.raise_if_cancelled()
            try:
                return operation()
            except _RETRYABLE_ERRORS:
                if attempt >= self._retry_policy.attempts:
                    raise
                if context is None:
                    time.sleep(self._retry_policy.delay_seconds)
                elif not context.wait(self._retry_policy.delay_seconds):
                    context.raise_if_cancelled()
        raise RuntimeError('SQL retry loop exhausted unexpectedly')


def build_select(plan: SqlSourcePlan) -> tuple[str, tuple[object, ...]]:
    definition = plan.definition
    projection = ', '.join(
        f'{_quote_identifier(column.source_name)} AS {_quote_identifier(column.output_name)}'
        for column in definition.columns
    )
    statement = f'SELECT {projection} FROM {_quote_identifier_path(definition.source_table)}'
    parameters: tuple[object, ...] = ()
    if definition.load_strategy is SqlLoadStrategy.SCOPED:
        if definition.scope_column is None or plan.scope is None:
            raise SqlDataProducerReadError('scoped source has no scope contract')
        placeholders = ', '.join('?' for _ in plan.scope.items)
        statement += f' WHERE {_quote_identifier(definition.scope_column)} IN ({placeholders})'
        parameters = tuple(plan.scope.values)
    return statement, parameters


def _batch_to_table(batch: SqlBatch, *, expected_columns: tuple[str, ...]) -> pa.Table:
    if not isinstance(batch, SqlBatch):
        raise SqlDataProducerReadError('SQL batch has an invalid type')
    if tuple(batch.columns) != expected_columns:
        raise SqlDataProducerReadError('SQL batch columns do not match the source definition')
    if not batch.rows:
        return pa.table({name: pa.array([], type=pa.null()) for name in expected_columns})
    columns = tuple(zip(*batch.rows, strict=True))
    try:
        return pa.table(
            {name: pa.array(values) for name, values in zip(expected_columns, columns, strict=True)}
        )
    except (pa.ArrowException, TypeError, ValueError) as error:
        raise SqlDataProducerReadError('SQL batch could not be converted to Arrow') from error


def _quote_identifier(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SqlDataProducerReadError('SQL identifier must be a non-empty string')
    normalized = value.strip().replace(']', ']]')
    return f'[{normalized}]'


def _quote_identifier_path(value: str) -> str:
    parts = tuple(part.strip() for part in value.split('.'))
    if not parts or any(not part for part in parts):
        raise SqlDataProducerReadError('SQL identifier path is invalid')
    return '.'.join(_quote_identifier(part) for part in parts)
