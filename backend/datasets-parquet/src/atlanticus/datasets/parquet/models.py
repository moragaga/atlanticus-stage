"""Opciones, filtros y resultados específicos de Parquet."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import pyarrow as pa

from atlanticus.datasets import DatasetPartKey, DatasetTarget
from atlanticus.datasets.parquet.errors import ParquetValidationError


class FilterOperator(StrEnum):
    """Operadores escalares combinables mediante AND."""

    EQUAL = 'eq'
    NOT_EQUAL = 'ne'
    GREATER_THAN = 'gt'
    GREATER_THAN_OR_EQUAL = 'ge'
    LESS_THAN = 'lt'
    LESS_THAN_OR_EQUAL = 'le'
    IN = 'in'


@dataclass(frozen=True, slots=True)
class ColumnFilter:
    """Predicado neutral que el store traduce a una expresión Arrow."""

    column: str
    operator: FilterOperator
    value: Any

    def __post_init__(self) -> None:
        if not isinstance(self.column, str) or not self.column:
            raise ParquetValidationError('filter column must be a non-empty string')
        if not isinstance(self.operator, FilterOperator):
            raise ParquetValidationError('filter operator must be a FilterOperator')
        if self.operator is FilterOperator.IN:
            if isinstance(self.value, str | bytes) or not isinstance(self.value, Iterable):
                raise ParquetValidationError('IN filter value must be a non-string iterable')
            values = tuple(self.value)
            if not values:
                raise ParquetValidationError('IN filter value must not be empty')
            object.__setattr__(self, 'value', values)
        elif self.value is None:
            raise ParquetValidationError('scalar filter value must not be None')


@dataclass(frozen=True, slots=True)
class ParquetWriteOptions:
    """Configuración estable de escritura, aislada de cada llamada."""

    compression: str = 'zstd'
    compression_level: int | None = 3
    use_dictionary: bool = True
    write_statistics: bool = True
    row_group_size: int | None = 131_072

    def __post_init__(self) -> None:
        if not isinstance(self.compression, str) or not self.compression.strip():
            raise ParquetValidationError('compression must be a non-empty string')
        if self.compression_level is not None and (
            not isinstance(self.compression_level, int) or isinstance(self.compression_level, bool)
        ):
            raise ParquetValidationError('compression_level must be an integer or None')
        if not isinstance(self.use_dictionary, bool):
            raise ParquetValidationError('use_dictionary must be a boolean')
        if not isinstance(self.write_statistics, bool):
            raise ParquetValidationError('write_statistics must be a boolean')
        if self.row_group_size is not None and (
            not isinstance(self.row_group_size, int)
            or isinstance(self.row_group_size, bool)
            or self.row_group_size <= 0
        ):
            raise ParquetValidationError('row_group_size must be greater than zero or None')


@dataclass(frozen=True, slots=True)
class ParquetPart:
    """Contenido completo que reemplaza una parte lógica."""

    key: DatasetPartKey
    table: pa.Table

    def __post_init__(self) -> None:
        if not isinstance(self.key, DatasetPartKey):
            raise ParquetValidationError('part key must be a DatasetPartKey')
        if not isinstance(self.table, pa.Table):
            raise ParquetValidationError('part table must be a pyarrow.Table')


@dataclass(frozen=True, slots=True)
class ParquetReadResult:
    """Tabla Arrow materializada junto con métricas físicas compactas."""

    table: pa.Table
    targets: tuple[DatasetTarget, ...]
    artifact_count: int
    size_bytes: int
    publication_tokens: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.table, pa.Table):
            raise ParquetValidationError('read result table must be a pyarrow.Table')
        try:
            targets = tuple(self.targets)
        except TypeError as error:
            raise ParquetValidationError(
                'read result targets must contain DatasetTarget values'
            ) from error
        if not targets or not all(isinstance(item, DatasetTarget) for item in targets):
            raise ParquetValidationError('read result targets must contain DatasetTarget values')
        if len(set(targets)) != len(targets):
            raise ParquetValidationError('read result targets must not contain duplicates')
        if isinstance(self.publication_tokens, str | bytes):
            raise ParquetValidationError('publication_tokens must contain non-empty strings')
        try:
            publication_tokens = tuple(self.publication_tokens)
        except TypeError as error:
            raise ParquetValidationError(
                'publication_tokens must contain non-empty strings'
            ) from error
        if isinstance(self.warnings, str | bytes):
            raise ParquetValidationError('warnings must contain non-empty strings')
        try:
            warnings = tuple(self.warnings)
        except TypeError as error:
            raise ParquetValidationError('warnings must contain non-empty strings') from error
        for field, value in (
            ('artifact_count', self.artifact_count),
            ('size_bytes', self.size_bytes),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ParquetValidationError(f'{field} must be a non-negative integer')
        if not all(isinstance(item, str) and item for item in publication_tokens):
            raise ParquetValidationError('publication_tokens must contain non-empty strings')
        if not all(isinstance(item, str) and item for item in warnings):
            raise ParquetValidationError('warnings must contain non-empty strings')
        object.__setattr__(self, 'targets', targets)
        object.__setattr__(self, 'publication_tokens', publication_tokens)
        object.__setattr__(self, 'warnings', warnings)

    @property
    def target_count(self) -> int:
        return len(self.targets)

    @property
    def row_count(self) -> int:
        return self.table.num_rows


@dataclass(frozen=True, slots=True)
class ParquetCleanupResult:
    """Resumen de artefactos propios eliminados fuera de la publicación vigente."""

    target: DatasetTarget
    temporary_count: int
    orphan_part_count: int
    reclaimed_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.target, DatasetTarget):
            raise ParquetValidationError('cleanup result target must be a DatasetTarget')
        for field, value in (
            ('temporary_count', self.temporary_count),
            ('orphan_part_count', self.orphan_part_count),
            ('reclaimed_bytes', self.reclaimed_bytes),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ParquetValidationError(f'{field} must be a non-negative integer')


@dataclass(frozen=True, slots=True)
class _Artifact:
    path: Path
    schema: pa.Schema
    item_count: int
    size_bytes: int
    content_signature: str | None
    part_value: str | None = None


@dataclass(frozen=True, slots=True)
class _ResolvedPublication:
    target: DatasetTarget
    schema: pa.Schema
    artifacts: tuple[_Artifact, ...]
    publication_token: str | None = None
    part_dimension: str | None = None
