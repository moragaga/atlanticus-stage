"""Persistencia física Parquet para los contratos neutrales de datasets."""

from atlanticus.datasets.parquet.errors import (
    ParquetCorruptionError,
    ParquetDatasetError,
    ParquetLayoutError,
    ParquetPublicationNotFoundError,
    ParquetReadError,
    ParquetSchemaError,
    ParquetValidationError,
    ParquetWriteError,
)
from atlanticus.datasets.parquet.models import (
    ColumnFilter,
    FilterOperator,
    ParquetCleanupResult,
    ParquetPart,
    ParquetReadResult,
    ParquetWriteOptions,
)
from atlanticus.datasets.parquet.store import ParquetDatasetStore

__version__ = '0.2.1'

__all__ = [
    'ColumnFilter',
    'FilterOperator',
    'ParquetCleanupResult',
    'ParquetCorruptionError',
    'ParquetDatasetError',
    'ParquetDatasetStore',
    'ParquetLayoutError',
    'ParquetPart',
    'ParquetPublicationNotFoundError',
    'ParquetReadError',
    'ParquetReadResult',
    'ParquetSchemaError',
    'ParquetValidationError',
    'ParquetWriteError',
    'ParquetWriteOptions',
    '__version__',
]
