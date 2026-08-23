"""Validaciones y normalizaciones privadas del adapter Parquet."""

from __future__ import annotations

from collections.abc import Iterable

import pyarrow as pa

from atlanticus.datasets.models import DatasetPartKey, DatasetTarget
from atlanticus.datasets.parquet.errors import ParquetSchemaError, ParquetValidationError
from atlanticus.datasets.parquet.models import ParquetPart


def _validate_table(table: pa.Table) -> None:
    if not isinstance(table, pa.Table):
        raise ParquetValidationError('table must be a pyarrow.Table')
    _validate_schema(table.schema)
    if table.num_columns == 0:
        raise ParquetValidationError('table must contain at least one column')


def _validate_schema(schema: pa.Schema) -> None:
    if len(schema.names) != len(set(schema.names)):
        raise ParquetSchemaError('schema column names must not contain duplicates')


def _validate_merge_columns(
    table: pa.Table,
    *,
    keys: tuple[str, ...],
    ordering: tuple[str, ...],
) -> None:
    for column in (*keys, *ordering):
        if column not in table.column_names:
            raise ParquetSchemaError(f'merge column does not exist: {column}')
    _validate_key_values(table, keys=keys)


def _validate_key_values(table: pa.Table, *, keys: tuple[str, ...]) -> None:
    for column in keys:
        if table[column].null_count:
            raise ParquetValidationError(f'merge key column must not contain nulls: {column}')


def _align_table(
    *,
    table: pa.Table,
    schema: pa.Schema,
    context: str,
) -> pa.Table:
    arrays: list[pa.ChunkedArray | pa.Array] = []
    for field in schema:
        if field.name not in table.column_names:
            arrays.append(pa.nulls(table.num_rows, type=field.type))
            continue
        column = table[field.name]
        if column.type != field.type:
            raise ParquetSchemaError(
                f'{context} has incompatible type for column {field.name}: '
                f'{column.type} != {field.type}'
            )
        arrays.append(column)
    return pa.Table.from_arrays(arrays, schema=schema)


def _normalize_incoming_parts(
    *,
    target: DatasetTarget,
    part_dimension: str,
    values: Iterable[ParquetPart],
) -> dict[str, ParquetPart]:
    if isinstance(values, str | bytes):
        raise ParquetValidationError('incoming_parts must be an iterable of ParquetPart')
    try:
        parts = tuple(values)
    except TypeError as error:
        raise ParquetValidationError('incoming_parts must be an iterable of ParquetPart') from error
    if not all(isinstance(item, ParquetPart) for item in parts):
        raise ParquetValidationError('incoming_parts must contain only ParquetPart values')
    normalized: dict[str, ParquetPart] = {}
    for part in parts:
        if part.key.target != target or part.key.dimension != part_dimension:
            raise ParquetValidationError('incoming part does not belong to the target layout')
        if part.key.value in normalized:
            raise ParquetValidationError(f'duplicate incoming part value: {part.key.value}')
        normalized[part.key.value] = part
    return normalized


def _normalize_remove_parts(
    *,
    target: DatasetTarget,
    part_dimension: str,
    values: Iterable[DatasetPartKey],
) -> set[str]:
    if isinstance(values, str | bytes):
        raise ParquetValidationError('remove_parts must be an iterable of DatasetPartKey')
    try:
        parts = tuple(values)
    except TypeError as error:
        raise ParquetValidationError(
            'remove_parts must be an iterable of DatasetPartKey'
        ) from error
    if not all(isinstance(item, DatasetPartKey) for item in parts):
        raise ParquetValidationError('remove_parts must contain only DatasetPartKey values')
    normalized: set[str] = set()
    for part in parts:
        if part.target != target or part.dimension != part_dimension:
            raise ParquetValidationError('removed part does not belong to the target layout')
        if part.value in normalized:
            raise ParquetValidationError(f'duplicate removed part value: {part.value}')
        normalized.add(part.value)
    return normalized


def _normalize_targets(values: Iterable[DatasetTarget]) -> tuple[DatasetTarget, ...]:
    if isinstance(values, DatasetTarget | str | bytes):
        raise ParquetValidationError('targets must be a non-string iterable of DatasetTarget')
    try:
        targets = tuple(values)
    except TypeError as error:
        raise ParquetValidationError('targets must be an iterable of DatasetTarget') from error
    if not targets or not all(isinstance(item, DatasetTarget) for item in targets):
        raise ParquetValidationError('targets must contain at least one DatasetTarget')
    if len(set(targets)) != len(targets):
        raise ParquetValidationError('targets must not contain duplicates')
    return targets


def _normalize_columns(
    values: Iterable[str],
    *,
    field: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if isinstance(values, str | bytes):
        raise ParquetValidationError(f'{field} must be a non-string iterable')
    try:
        columns = tuple(values)
    except TypeError as error:
        raise ParquetValidationError(f'{field} must be an iterable of strings') from error
    if not allow_empty and not columns:
        raise ParquetValidationError(f'{field} must not be empty')
    if not all(isinstance(column, str) and column for column in columns):
        raise ParquetValidationError(f'{field} must contain non-empty strings')
    if len(set(columns)) != len(columns):
        raise ParquetValidationError(f'{field} must not contain duplicates')
    return columns
