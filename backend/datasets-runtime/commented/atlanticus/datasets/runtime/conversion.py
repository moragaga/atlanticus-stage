"""Conversiones tabulares explícitas compartidas por la fachada runtime."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd
import pyarrow as pa

from atlanticus.datasets.runtime.errors import (
    DatasetConversionError,
    DatasetRuntimeValidationError,
)

type TabularData = pd.DataFrame | pa.Table


# PyArrow es el formato interno del store; Pandas sólo existe en esta frontera de comodidad.
def to_arrow_table(data: TabularData, *, field: str = 'data') -> pa.Table:
    """Entrega Arrow sin conservar índices implícitos de Pandas."""

    # Una tabla Arrow es inmutable y puede atravesar la frontera sin una copia innecesaria.
    if isinstance(data, pa.Table):
        _validate_columns(data.column_names, field=field)
        return data
    if not isinstance(data, pd.DataFrame):
        raise DatasetRuntimeValidationError(f'{field} must be a pandas.DataFrame or pyarrow.Table')
    # Se valida antes de convertir para impedir que PyArrow normalice nombres inválidos.
    _validate_columns(data.columns, field=field)
    try:
        # El índice no forma parte del contrato: un índice significativo debe ser columna explícita.
        table = pa.Table.from_pandas(data, preserve_index=False)
    except (pa.ArrowException, TypeError, ValueError) as error:
        raise DatasetConversionError(f'could not convert {field} from pandas to pyarrow') from error
    # El schema físico no conserva instrucciones privadas para reconstruir el objeto Pandas fuente.
    table = _without_pandas_metadata(table)
    _validate_columns(table.column_names, field=field)
    return table


def to_pandas_dataframe(table: pa.Table) -> pd.DataFrame:
    """Crea un DataFrame nuevo usando la representación convencional de Pandas."""

    if not isinstance(table, pa.Table):
        raise DatasetRuntimeValidationError('table must be a pyarrow.Table')
    _validate_columns(table.column_names, field='table')
    try:
        # No se fuerza ArrowDtype; los motores reciben un DataFrame convencional de Pandas.
        dataframe = table.to_pandas()
    except (pa.ArrowException, TypeError, ValueError) as error:
        raise DatasetConversionError('could not convert table from pyarrow to pandas') from error
    if not isinstance(dataframe, pd.DataFrame):
        raise DatasetConversionError('pyarrow conversion did not return a pandas.DataFrame')
    return dataframe


def normalize_column_names(
    values: Iterable[str],
    *,
    field: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    """Normaliza una colección de columnas sin aceptar defaults ambiguos."""

    # Un string es iterable, pero nunca representa correctamente una colección de columnas.
    if isinstance(values, str | bytes):
        raise DatasetRuntimeValidationError(f'{field} must be an iterable of column names')
    try:
        columns = tuple(values)
    except TypeError as error:
        raise DatasetRuntimeValidationError(
            f'{field} must be an iterable of column names'
        ) from error
    if not allow_empty and not columns:
        raise DatasetRuntimeValidationError(f'{field} must not be empty')
    _validate_columns(columns, field=field)
    return columns


def validate_merge_table(
    table: pa.Table,
    *,
    key_columns: tuple[str, ...],
    order_by: tuple[str, ...],
) -> None:
    """Comprueba claves y ordenamiento antes de invocar el store físico."""

    # Estas comprobaciones entregan errores del runtime antes de tocar el almacenamiento físico.
    available = set(table.column_names)
    missing_keys = tuple(column for column in key_columns if column not in available)
    if missing_keys:
        raise DatasetRuntimeValidationError(f'missing merge key columns: {list(missing_keys)}')
    missing_ordering = tuple(column for column in order_by if column not in available)
    if missing_ordering:
        raise DatasetRuntimeValidationError(
            f'missing merge order columns: {list(missing_ordering)}'
        )
    null_keys = tuple(column for column in key_columns if table[column].null_count > 0)
    if null_keys:
        raise DatasetRuntimeValidationError(f'merge key columns contain nulls: {list(null_keys)}')


def _validate_columns(values: Iterable[object], *, field: str) -> None:
    # La regla es común a entradas Pandas, tablas Arrow, claves y proyecciones.
    columns = tuple(values)
    invalid = tuple(value for value in columns if not isinstance(value, str) or not value.strip())
    if invalid:
        raise DatasetRuntimeValidationError(f'{field} column names must be non-empty strings')
    if len(set(columns)) != len(columns):
        raise DatasetRuntimeValidationError(f'{field} column names must not contain duplicates')


def _without_pandas_metadata(table: pa.Table) -> pa.Table:
    # Se retira sólo la clave reservada de Pandas; cualquier metadato Arrow explícito se conserva.
    metadata = dict(table.schema.metadata or {})
    metadata.pop(b'pandas', None)
    return table.replace_schema_metadata(metadata or None)
