# Traduce una definición de catálogo a lecturas MSSQL por lotes y aplica retry transitorio.
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import TypeVar

import pyarrow as pa

from ada.processes.blockgrade.errors import BlockgradeSqlReadError
from ada.processes.blockgrade.models import BlockgradeLoadStrategy, BlockgradeSourceDefinition, BlockgradeSourcePlan
from ada.processes.blockgrade.settings import BlockgradeSqlRetryPolicy
from atlanticus.connectivity.sql import (
    SqlBatch,
    SqlClient,
    SqlConnectionError,
    SqlTableChangeMarker,
    SqlTimeoutError,
)
from atlanticus.runtime import JobRuntimeContext

_T = TypeVar('_T')
_RETRYABLE_ERRORS = (SqlConnectionError, SqlTimeoutError)


class BlockgradeSqlReader:
    def __init__(
        self,
        *,
        sql: SqlClient,
        retry_policy: BlockgradeSqlRetryPolicy | None = None,
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
        self._retry_policy = retry_policy or BlockgradeSqlRetryPolicy()
        self._max_rows = resolved_max_rows

    def read_change_markers(
        self,
        definitions: Sequence[BlockgradeSourceDefinition],
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
                raise BlockgradeSqlReadError(
                    f'SQL change marker is missing for source table: {definition.source_table}'
                )
            resolved[definition.source_key] = marker
        return resolved

    def read_source(
        self,
        plan: BlockgradeSourcePlan,
        *,
        context: JobRuntimeContext | None = None,
    ) -> pa.Table:
        if not isinstance(plan, BlockgradeSourcePlan):
            raise TypeError('plan must be a BlockgradeSourcePlan')
        return self._run_with_retry(
            lambda: self._read_source_once(plan, context=context),
            context=context,
        )

    def _read_source_once(
        self,
        plan: BlockgradeSourcePlan,
        *,
        context: JobRuntimeContext | None = None,
    ) -> pa.Table:
        statement, parameters = _build_select(plan)
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
                    raise BlockgradeSqlReadError(
                        f'Blockgrade source exceeded the configured row limit: {plan.definition.source_key}'
                    )
                tables.append(table)
        if context is not None:
            context.raise_if_cancelled()
        if not tables:
            return pa.table({name: pa.array([], type=pa.null()) for name in expected_columns})
        try:
            return pa.concat_tables(tables, promote_options='permissive')
        except (pa.ArrowException, TypeError, ValueError) as error:
            raise BlockgradeSqlReadError('SQL batches could not be combined as Arrow data') from error

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


def _build_select(plan: BlockgradeSourcePlan) -> tuple[str, tuple[object, ...]]:
    definition = plan.definition
    projection = ', '.join(
        f'{_quote_identifier(column.source_name)} AS {_quote_identifier(column.output_name)}'
        for column in definition.columns
    )
    statement = f'SELECT {projection} FROM {_quote_identifier_path(definition.source_table)}'
    parameters: tuple[object, ...] = ()
    if definition.load_strategy is BlockgradeLoadStrategy.SHIFT_WINDOW:
        if definition.shift_id_column is None:
            raise BlockgradeSqlReadError('shift_window source has no shift_id_column')
        placeholders = ', '.join('?' for _ in plan.shift_ids)
        statement += f' WHERE {_quote_identifier(definition.shift_id_column)} IN ({placeholders})'
        parameters = tuple(plan.shift_ids)
    return statement, parameters


def _batch_to_table(batch: SqlBatch, *, expected_columns: tuple[str, ...]) -> pa.Table:
    if batch.columns != expected_columns:
        raise BlockgradeSqlReadError('SQL source result columns do not match the Blockgrade catalog')
    columns = tuple(zip(*batch.rows, strict=True))
    try:
        return pa.Table.from_arrays(
            [pa.array(values) for values in columns],
            names=expected_columns,
        )
    except (pa.ArrowException, TypeError, ValueError) as error:
        raise BlockgradeSqlReadError('SQL batch could not be converted to Arrow') from error


def _quote_identifier_path(value: str) -> str:
    parts = tuple(part.strip() for part in value.split('.'))
    if len(parts) != 2 or any(not part for part in parts):
        raise BlockgradeSqlReadError('SQL source table identifier must use schema.table format')
    return '.'.join(_quote_identifier(part) for part in parts)


def _quote_identifier(value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise BlockgradeSqlReadError('SQL identifier is invalid')
    escaped = normalized.replace(']', ']]')
    return f'[{escaped}]'
