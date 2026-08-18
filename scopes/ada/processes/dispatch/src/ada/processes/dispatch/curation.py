from __future__ import annotations

from datetime import UTC, datetime

import pyarrow as pa
import pyarrow.compute as pc

from ada.processes.dispatch.errors import DispatchSchemaError
from ada.processes.dispatch.models import (
    DispatchColumnDefinition,
    DispatchSourceDefinition,
    DispatchValueKind,
)


def curate_dispatch_table(
    *,
    definition: DispatchSourceDefinition,
    table: pa.Table,
) -> pa.Table:
    if not isinstance(definition, DispatchSourceDefinition):
        raise DispatchSchemaError('definition must be a DispatchSourceDefinition')
    if not isinstance(table, pa.Table):
        raise DispatchSchemaError('table must be a pyarrow.Table')
    if tuple(table.column_names) != definition.expected_output_columns:
        raise DispatchSchemaError('Dispatch input columns do not match the source definition')
    fields: list[pa.Field] = []
    arrays: list[pa.Array | pa.ChunkedArray] = []
    for column in definition.columns:
        field_type = _field_type(column.value_kind)
        array = _cast_column(table[column.output_name], column=column)
        if column.required and array.null_count:
            raise DispatchSchemaError(
                f'Dispatch required column {column.output_name} contains null values'
            )
        fields.append(pa.field(column.output_name, field_type, nullable=not column.required))
        arrays.append(array)
    return pa.Table.from_arrays(arrays, schema=pa.schema(fields))


def source_last_update_utc(
    *,
    definition: DispatchSourceDefinition,
    table: pa.Table,
) -> datetime | None:
    column_name = definition.source_last_update_output_column
    if column_name is None or table.num_rows == 0:
        return None
    scalar = pc.max(table[column_name])
    value = scalar.as_py() if scalar is not None else None
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DispatchSchemaError('Dispatch source last update must be timezone-aware')
    return value.astimezone(UTC)


def _cast_column(
    values: pa.ChunkedArray,
    *,
    column: DispatchColumnDefinition,
) -> pa.Array | pa.ChunkedArray:
    try:
        if column.value_kind is DispatchValueKind.DATE:
            if pa.types.is_null(values.type):
                return pa.nulls(len(values), type=pa.date32())
            if not (pa.types.is_date(values.type) or pa.types.is_timestamp(values.type)):
                raise DispatchSchemaError(
                    f'Dispatch date column {column.output_name} must use an Arrow date or timestamp type'
                )
            return pc.cast(values, pa.date32(), safe=True)
        if column.value_kind is DispatchValueKind.DATETIME:
            return _cast_datetime(values, column=column)
        if pa.types.is_null(values.type):
            return pa.nulls(len(values), type=_field_type(column.value_kind))
        return pc.cast(values, _field_type(column.value_kind), safe=True)
    except DispatchSchemaError:
        raise
    except (pa.ArrowException, TypeError, ValueError) as error:
        raise DispatchSchemaError(
            f'Dispatch column {column.output_name} is incompatible with {column.value_kind.value}'
        ) from error


def _cast_datetime(
    values: pa.ChunkedArray,
    *,
    column: DispatchColumnDefinition,
) -> pa.Array | pa.ChunkedArray:
    if pa.types.is_null(values.type):
        return pa.nulls(len(values), type=pa.timestamp('us', tz='UTC'))
    if not pa.types.is_timestamp(values.type):
        raise DispatchSchemaError(
            f'Dispatch datetime column {column.output_name} must use an Arrow timestamp type'
        )
    normalized: pa.Array | pa.ChunkedArray = values
    if values.type.tz is None:
        if column.source_timezone is None:
            raise DispatchSchemaError(
                f'Dispatch datetime column {column.output_name} requires source_timezone'
            )
        normalized = pc.assume_timezone(
            values,
            column.source_timezone,
            ambiguous='raise',
            nonexistent='raise',
        )
    return pc.cast(normalized, pa.timestamp('us', tz='UTC'), safe=True)


def _field_type(value_kind: DispatchValueKind) -> pa.DataType:
    if value_kind is DispatchValueKind.TEXT:
        return pa.string()
    if value_kind is DispatchValueKind.INTEGER:
        return pa.int64()
    if value_kind is DispatchValueKind.FLOAT:
        return pa.float64()
    if value_kind is DispatchValueKind.BOOLEAN:
        return pa.bool_()
    if value_kind is DispatchValueKind.DATE:
        return pa.date32()
    return pa.timestamp('us', tz='UTC')
