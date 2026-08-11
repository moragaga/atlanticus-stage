"""Entradas y resultados tabulares de la fachada runtime."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pyarrow as pa

from atlanticus.datasets import DatasetPartKey, DatasetTarget
from atlanticus.datasets.runtime.conversion import TabularData
from atlanticus.datasets.runtime.errors import DatasetRuntimeValidationError


@dataclass(frozen=True, slots=True)
class RuntimeDatasetPart:
    """Contenido completo de una parte, expresado en Pandas o PyArrow."""

    key: DatasetPartKey
    data: TabularData

    def __post_init__(self) -> None:
        if not isinstance(self.key, DatasetPartKey):
            raise DatasetRuntimeValidationError('part key must be a DatasetPartKey')
        if not isinstance(self.data, pd.DataFrame | pa.Table):
            raise DatasetRuntimeValidationError(
                'part data must be a pandas.DataFrame or pyarrow.Table'
            )


@dataclass(frozen=True, slots=True)
class TableReadResult:
    """Tabla Arrow junto con la identidad y métricas de la lectura física."""

    table: pa.Table
    targets: tuple[DatasetTarget, ...]
    artifact_count: int
    size_bytes: int
    publication_tokens: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.table, pa.Table):
            raise DatasetRuntimeValidationError('read result table must be a pyarrow.Table')
        _validate_metadata(
            targets=self.targets,
            artifact_count=self.artifact_count,
            size_bytes=self.size_bytes,
            publication_tokens=self.publication_tokens,
            warnings=self.warnings,
        )

    @property
    def target_count(self) -> int:
        return len(self.targets)

    @property
    def row_count(self) -> int:
        return self.table.num_rows


@dataclass(frozen=True, slots=True)
class DataFrameReadResult:
    """DataFrame nuevo junto con la identidad y métricas de la lectura física."""

    dataframe: pd.DataFrame
    targets: tuple[DatasetTarget, ...]
    artifact_count: int
    size_bytes: int
    publication_tokens: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.dataframe, pd.DataFrame):
            raise DatasetRuntimeValidationError('read result dataframe must be a pandas.DataFrame')
        _validate_metadata(
            targets=self.targets,
            artifact_count=self.artifact_count,
            size_bytes=self.size_bytes,
            publication_tokens=self.publication_tokens,
            warnings=self.warnings,
        )

    @property
    def target_count(self) -> int:
        return len(self.targets)

    @property
    def row_count(self) -> int:
        return len(self.dataframe)


def _validate_metadata(
    *,
    targets: tuple[DatasetTarget, ...],
    artifact_count: int,
    size_bytes: int,
    publication_tokens: tuple[str, ...],
    warnings: tuple[str, ...],
) -> None:
    if not isinstance(targets, tuple):
        raise DatasetRuntimeValidationError('read result targets must be a tuple')
    if not targets or not all(isinstance(item, DatasetTarget) for item in targets):
        raise DatasetRuntimeValidationError('read result targets must contain DatasetTarget values')
    if len(set(targets)) != len(targets):
        raise DatasetRuntimeValidationError('read result targets must not contain duplicates')
    for field, value in (('artifact_count', artifact_count), ('size_bytes', size_bytes)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise DatasetRuntimeValidationError(f'{field} must be a non-negative integer')
    if not isinstance(publication_tokens, tuple):
        raise DatasetRuntimeValidationError('publication_tokens must be a tuple')
    if not all(isinstance(item, str) and item for item in publication_tokens):
        raise DatasetRuntimeValidationError('publication_tokens must contain non-empty strings')
    if not isinstance(warnings, tuple):
        raise DatasetRuntimeValidationError('warnings must be a tuple')
    if not all(isinstance(item, str) and item for item in warnings):
        raise DatasetRuntimeValidationError('warnings must contain non-empty strings')
