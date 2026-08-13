# Espejo comentado en español del archivo productivo equivalente.
# Sólo agrega explicación pedagógica; el contrato ejecutable es idéntico.
"""Modelos neutrales que no exponen conexiones, cursores ni filas del driver SQL."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


# Los modelos quedan independientes del driver concreto.
class SqlTimeoutPhase(StrEnum):
    """Fase exacta en que la operación SQL agotó el tiempo configurado."""

    CONNECT = 'connect'
    QUERY = 'query'


@dataclass(frozen=True, slots=True)
class SqlTableChangeMarker:
    """Marca volátil de cambios DML observada para una tabla SQL Server."""

    source_table: str
    generation_token: str
    last_user_update_token: str | None
    user_updates: int

    def __post_init__(self) -> None:
        source_table = str(self.source_table).strip()
        generation_token = str(self.generation_token).strip()
        last_user_update_token = (
            None
            if self.last_user_update_token is None
            else str(self.last_user_update_token).strip() or None
        )
        if not source_table:
            raise ValueError('source_table must not be empty')
        if not generation_token:
            raise ValueError('generation_token must not be empty')
        if not isinstance(self.user_updates, int) or isinstance(self.user_updates, bool):
            raise ValueError('user_updates must be a non-negative integer')
        if self.user_updates < 0:
            raise ValueError('user_updates must be a non-negative integer')
        object.__setattr__(self, 'source_table', source_table)
        object.__setattr__(self, 'generation_token', generation_token)
        object.__setattr__(self, 'last_user_update_token', last_user_update_token)


@dataclass(frozen=True, slots=True)
class SqlResult:
    """Resultado pequeño y acotado cargado completamente en memoria."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    duration_ms: float

    def __post_init__(self) -> None:
        columns = tuple(str(column) for column in self.columns)
        rows = tuple(tuple(row) for row in self.rows)
        _validate_shape(columns=columns, rows=rows)
        if not math.isfinite(self.duration_ms) or self.duration_ms < 0:
            raise ValueError('duration_ms must be a finite value greater than or equal to zero')
        object.__setattr__(self, 'columns', columns)
        object.__setattr__(self, 'rows', rows)

    @property
    def row_count(self) -> int:
        """Retorna la cantidad exacta de filas materializadas."""

        return len(self.rows)


@dataclass(frozen=True, slots=True)
class SqlBatch:
    """Fragmento neutral de una consulta grande con posición explícita."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    batch_number: int
    row_offset: int
    duration_ms: float

    def __post_init__(self) -> None:
        columns = tuple(str(column) for column in self.columns)
        rows = tuple(tuple(row) for row in self.rows)
        _validate_shape(columns=columns, rows=rows)
        if not rows:
            raise ValueError('SqlBatch rows must not be empty')
        if self.batch_number <= 0:
            raise ValueError('batch_number must be greater than zero')
        if self.row_offset < 0:
            raise ValueError('row_offset must be greater than or equal to zero')
        if not math.isfinite(self.duration_ms) or self.duration_ms < 0:
            raise ValueError('duration_ms must be a finite value greater than or equal to zero')
        object.__setattr__(self, 'columns', columns)
        object.__setattr__(self, 'rows', rows)

    @property
    def row_count(self) -> int:
        """Retorna la cantidad exacta de filas contenidas en el lote."""

        return len(self.rows)


def _validate_shape(*, columns: tuple[str, ...], rows: tuple[tuple[Any, ...], ...]) -> None:
    if not columns:
        raise ValueError('SQL result columns must not be empty')
    if any(not column.strip() for column in columns):
        raise ValueError('SQL result column names must not be empty')
    if any(len(row) != len(columns) for row in rows):
        raise ValueError('SQL result rows must match the column count')
