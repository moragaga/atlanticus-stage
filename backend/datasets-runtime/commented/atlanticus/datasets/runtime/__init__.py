"""Fachada bidireccional Pandas y PyArrow para datasets Atlanticus."""

# Esta es la única puerta pública que necesitan KPI, alarmas y adaptadores de fuente.
from atlanticus.datasets.parquet import ColumnFilter, FilterOperator
from atlanticus.datasets.runtime.conversion import TabularData, to_arrow_table, to_pandas_dataframe
from atlanticus.datasets.runtime.errors import (
    DatasetConversionError,
    DatasetRuntimeError,
    DatasetRuntimeNotFoundError,
    DatasetRuntimeReadError,
    DatasetRuntimeValidationError,
    DatasetRuntimeWriteError,
)
from atlanticus.datasets.runtime.facade import DatasetRuntime
from atlanticus.datasets.runtime.models import (
    DataFrameReadResult,
    RuntimeDatasetPart,
    TableReadResult,
)

__version__ = '0.1.0'

# La lista explícita evita que detalles internos se conviertan accidentalmente en contrato público.
__all__ = [
    'ColumnFilter',
    'DataFrameReadResult',
    'DatasetConversionError',
    'DatasetRuntime',
    'DatasetRuntimeError',
    'DatasetRuntimeNotFoundError',
    'DatasetRuntimeReadError',
    'DatasetRuntimeValidationError',
    'DatasetRuntimeWriteError',
    'FilterOperator',
    'RuntimeDatasetPart',
    'TableReadResult',
    'TabularData',
    '__version__',
    'to_arrow_table',
    'to_pandas_dataframe',
]
