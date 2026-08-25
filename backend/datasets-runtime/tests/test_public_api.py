from __future__ import annotations

import atlanticus.datasets.runtime as runtime_package


def test_public_api_and_version_are_explicit() -> None:
    assert runtime_package.__version__ == '0.2.1'
    assert set(runtime_package.__all__) == {
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
    }
