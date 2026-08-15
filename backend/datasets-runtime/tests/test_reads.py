from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pytest

from atlanticus.datasets import DatasetDefinition
from atlanticus.datasets.runtime import (
    ColumnFilter,
    DataFrameReadResult,
    DatasetRuntime,
    DatasetRuntimeNotFoundError,
    DatasetRuntimeValidationError,
    FilterOperator,
    TableReadResult,
)


def _target(definition: DatasetDefinition, *, day: str):
    return definition.resolve_target(
        materialization='granular',
        partition={'year': '2026', 'month': '07', 'day': day},
    )


def test_read_table_and_dataframe_preserve_physical_metadata(
    dataset_runtime: DatasetRuntime,
    pi_definition: DatasetDefinition,
) -> None:
    target = _target(pi_definition, day='21')
    dataset_runtime.replace(
        definition=pi_definition,
        target=target,
        data=pa.table({'timestamp': [1, 2], 'value': [10.0, 20.0]}),
    )

    table_result = dataset_runtime.read_table(
        definition=pi_definition,
        target=target,
    )
    dataframe_result = dataset_runtime.read_dataframe(
        definition=pi_definition,
        target=target,
    )

    assert isinstance(table_result, TableReadResult)
    assert isinstance(dataframe_result, DataFrameReadResult)
    assert table_result.targets == dataframe_result.targets == (target,)
    assert table_result.row_count == dataframe_result.row_count == 2
    assert table_result.artifact_count == dataframe_result.artifact_count == 1
    assert table_result.size_bytes == dataframe_result.size_bytes


def test_each_dataframe_read_is_independent_and_the_runtime_has_no_cache(
    dataset_runtime: DatasetRuntime,
    pi_definition: DatasetDefinition,
) -> None:
    target = _target(pi_definition, day='21')
    dataset_runtime.replace(
        definition=pi_definition,
        target=target,
        data=pd.DataFrame({'value': [10.0]}),
    )

    first = dataset_runtime.read_dataframe(
        definition=pi_definition,
        target=target,
    ).dataframe
    first.loc[0, 'value'] = 999.0
    second = dataset_runtime.read_dataframe(
        definition=pi_definition,
        target=target,
    ).dataframe

    assert first is not second
    assert second['value'].tolist() == [10.0]


def test_scan_table_and_dataframe_apply_projection_filters_and_targets(
    dataset_runtime: DatasetRuntime,
    pi_definition: DatasetDefinition,
) -> None:
    day_20 = _target(pi_definition, day='20')
    day_21 = _target(pi_definition, day='21')
    dataset_runtime.replace(
        definition=pi_definition,
        target=day_20,
        data=pd.DataFrame({'timestamp': [1, 2], 'value': [10.0, 20.0], 'unused': [1, 1]}),
    )
    dataset_runtime.replace(
        definition=pi_definition,
        target=day_21,
        data=pa.table({'timestamp': [3, 4], 'value': [30.0, 40.0], 'unused': [2, 2]}),
    )
    filters = (
        ColumnFilter(
            column='timestamp',
            operator=FilterOperator.GREATER_THAN_OR_EQUAL,
            value=2,
        ),
    )

    table_result = dataset_runtime.scan_table(
        definition=pi_definition,
        targets=(day_20, day_21),
        columns=('timestamp', 'value'),
        filters=filters,
    )
    dataframe_result = dataset_runtime.scan_dataframe(
        definition=pi_definition,
        targets=(day_20, day_21),
        columns=('value',),
        filters=filters,
    )

    assert table_result.table.to_pydict() == {
        'timestamp': [2, 3, 4],
        'value': [20.0, 30.0, 40.0],
    }
    assert dataframe_result.dataframe['value'].tolist() == [20.0, 30.0, 40.0]
    assert table_result.target_count == dataframe_result.target_count == 2
    assert table_result.artifact_count == dataframe_result.artifact_count == 2


@pytest.mark.parametrize(
    ('result_type', 'data_field', 'data'),
    (
        (TableReadResult, 'table', pa.table({'value': [1]})),
        (DataFrameReadResult, 'dataframe', pd.DataFrame({'value': [1]})),
    ),
)
@pytest.mark.parametrize('mutable_field', ('targets', 'publication_tokens', 'warnings'))
def test_read_results_reject_mutable_metadata_collections(
    pi_definition: DatasetDefinition,
    result_type: type[TableReadResult] | type[DataFrameReadResult],
    data_field: str,
    data: pa.Table | pd.DataFrame,
    mutable_field: str,
) -> None:
    values = {
        data_field: data,
        'targets': (_target(pi_definition, day='21'),),
        'artifact_count': 1,
        'size_bytes': 10,
        'publication_tokens': ('token',),
        'warnings': ('warning',),
    }
    values[mutable_field] = list(values[mutable_field])

    with pytest.raises(DatasetRuntimeValidationError, match='must be a tuple'):
        result_type(**values)  # type: ignore[arg-type]


def test_read_schema_returns_confirmed_schema_without_rows(
    dataset_runtime: DatasetRuntime,
    pi_definition: DatasetDefinition,
) -> None:
    target = _target(pi_definition, day='21')
    table = pa.table({'timestamp': [1, 2], 'value': [10.0, 20.0]})
    dataset_runtime.replace(definition=pi_definition, target=target, data=table)

    schema = dataset_runtime.read_schema(definition=pi_definition, target=target)

    assert schema.equals(table.schema, check_metadata=True)


def test_read_schema_maps_missing_publication_to_not_found(
    dataset_runtime: DatasetRuntime,
    pi_definition: DatasetDefinition,
) -> None:
    target = _target(pi_definition, day='21')

    with pytest.raises(DatasetRuntimeNotFoundError, match='no confirmed publication'):
        dataset_runtime.read_schema(definition=pi_definition, target=target)
