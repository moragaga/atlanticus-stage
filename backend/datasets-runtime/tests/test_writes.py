from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pytest

from atlanticus.datasets import DatasetDefinition, PublicationStatus
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import (
    DatasetRuntime,
    DatasetRuntimeValidationError,
    RuntimeDatasetPart,
)


def _pi_target(definition: DatasetDefinition, *, day: str = '21'):
    return definition.resolve_target(
        materialization='granular',
        partition={'year': '2026', 'month': '07', 'day': day},
    )


def _dispatch_target(definition: DatasetDefinition):
    return definition.resolve_target(
        materialization='operational-day',
        partition={'year': '2026', 'month': '07', 'day': '21'},
    )


def test_replace_accepts_dataframe_and_persists_no_index(
    dataset_runtime: DatasetRuntime,
    parquet_store: ParquetDatasetStore,
    pi_definition: DatasetDefinition,
) -> None:
    target = _pi_target(pi_definition)
    dataframe = pd.DataFrame(
        {'timestamp': pd.to_datetime(('2026-07-21T10:00:00Z',), utc=True), 'value': [10.0]},
        index=pd.Index((50,), name='source_index'),
    )

    result = dataset_runtime.replace(
        definition=pi_definition,
        target=target,
        data=dataframe,
    )
    stored = parquet_store.read(definition=pi_definition, target=target).table

    assert result.status is PublicationStatus.COMMITTED
    assert stored.column_names == ['timestamp', 'value']
    assert stored['value'].to_pylist() == [10.0]
    assert dataframe.index.name == 'source_index'


def test_pi_poc_merges_pandas_and_incoming_nulls_replace_previous_values(
    dataset_runtime: DatasetRuntime,
    pi_definition: DatasetDefinition,
) -> None:
    target = _pi_target(pi_definition)
    dataset_runtime.replace(
        definition=pi_definition,
        target=target,
        data=pd.DataFrame(
            {
                'timestamp': pd.to_datetime(
                    ('2026-07-21T10:00:00Z', '2026-07-21T11:00:00Z'),
                    utc=True,
                ),
                'tk10_nivel_inst': [10.0, 11.0],
                'retired_tag': [100.0, 110.0],
            }
        ),
    )
    incoming = pd.DataFrame(
        {
            'timestamp': pd.to_datetime(
                ('2026-07-21T11:00:00Z', '2026-07-21T12:00:00Z'),
                utc=True,
            ),
            'tk10_nivel_inst': pd.Series((None, 12.0), dtype='Float64'),
            'tk12_nivel_inst': [21.0, 22.0],
        }
    )

    result = dataset_runtime.merge(
        definition=pi_definition,
        target=target,
        data=incoming,
        key_columns=('timestamp',),
        order_by=('timestamp',),
    )
    merged = dataset_runtime.read_dataframe(
        definition=pi_definition,
        target=target,
    ).dataframe

    assert result.status is PublicationStatus.COMMITTED
    assert list(merged.columns) == ['timestamp', 'tk10_nivel_inst', 'tk12_nivel_inst']
    assert merged['tk10_nivel_inst'].iloc[[0, 2]].tolist() == [10.0, 12.0]
    assert pd.isna(merged['tk10_nivel_inst'].iloc[1])
    assert merged['tk12_nivel_inst'].iloc[[1, 2]].tolist() == [21.0, 22.0]
    assert pd.isna(merged['tk12_nivel_inst'].iloc[0])


def test_merge_validates_keys_before_the_store(
    dataset_runtime: DatasetRuntime,
    pi_definition: DatasetDefinition,
) -> None:
    target = _pi_target(pi_definition)

    with pytest.raises(DatasetRuntimeValidationError, match='missing merge key columns'):
        dataset_runtime.merge(
            definition=pi_definition,
            target=target,
            data=pd.DataFrame({'value': [1]}),
            key_columns=('timestamp',),
        )

    with pytest.raises(DatasetRuntimeValidationError, match='contain nulls'):
        dataset_runtime.merge(
            definition=pi_definition,
            target=target,
            data=pa.table(
                {
                    'timestamp': pa.array([None], type=pa.timestamp('us', tz='UTC')),
                    'value': [1],
                }
            ),
            key_columns=('timestamp',),
        )


def test_empty_replace_and_merge_skip_without_creating_target(
    dataset_runtime: DatasetRuntime,
    parquet_store: ParquetDatasetStore,
    pi_definition: DatasetDefinition,
) -> None:
    replace_target = _pi_target(pi_definition, day='20')
    merge_target = _pi_target(pi_definition, day='21')
    empty = pd.DataFrame(
        {
            'timestamp': pd.Series(dtype='datetime64[ns, UTC]'),
            'value': pd.Series(dtype='float64'),
        }
    )

    replace_result = dataset_runtime.replace(
        definition=pi_definition,
        target=replace_target,
        data=empty,
    )
    merge_result = dataset_runtime.merge(
        definition=pi_definition,
        target=merge_target,
        data=empty,
        key_columns=('timestamp',),
    )

    assert replace_result.status is PublicationStatus.SKIPPED
    assert merge_result.status is PublicationStatus.SKIPPED
    assert not parquet_store.path_for(
        definition=pi_definition,
        target=replace_target,
    ).exists()
    assert not parquet_store.path_for(
        definition=pi_definition,
        target=merge_target,
    ).exists()


def test_dispatch_poc_publishes_mixed_pandas_and_arrow_parts_atomically(
    dataset_runtime: DatasetRuntime,
    dispatch_definition: DatasetDefinition,
) -> None:
    target = _dispatch_target(dispatch_definition)
    part_001 = dispatch_definition.resolve_part(target=target, value='26199001')
    part_002 = dispatch_definition.resolve_part(target=target, value='26199002')

    result = dataset_runtime.publish_parts(
        definition=dispatch_definition,
        target=target,
        parts=(
            RuntimeDatasetPart(
                key=part_001,
                data=pd.DataFrame(
                    {'shift_id': [26199001], 'equipment': ['TRUCK-1'], 'tonnage': [100.0]}
                ),
            ),
            RuntimeDatasetPart(
                key=part_002,
                data=pa.table(
                    {
                        'shift_id': pa.array([26199002], type=pa.int64()),
                        'equipment': pa.array(['TRUCK-2'], type=pa.large_string()),
                        'tonnage': pa.array([200.0], type=pa.float64()),
                    }
                ),
            ),
        ),
    )
    table = dataset_runtime.read_table(
        definition=dispatch_definition,
        target=target,
    ).table

    assert result.status is PublicationStatus.COMMITTED
    assert sorted(table['shift_id'].to_pylist()) == [26199001, 26199002]

    removed = dataset_runtime.publish_parts(
        definition=dispatch_definition,
        target=target,
        remove_parts=(part_001,),
    )
    remaining = dataset_runtime.read_table(
        definition=dispatch_definition,
        target=target,
    ).table

    assert removed.status is PublicationStatus.COMMITTED
    assert remaining['shift_id'].to_pylist() == [26199002]


def test_empty_part_skips_the_whole_composition_and_preserves_publication(
    dataset_runtime: DatasetRuntime,
    dispatch_definition: DatasetDefinition,
) -> None:
    target = _dispatch_target(dispatch_definition)
    part_001 = dispatch_definition.resolve_part(target=target, value='26199001')
    part_002 = dispatch_definition.resolve_part(target=target, value='26199002')
    dataset_runtime.publish_parts(
        definition=dispatch_definition,
        target=target,
        parts=(
            RuntimeDatasetPart(
                key=part_001,
                data=pa.table({'shift_id': [26199001], 'tonnage': [100.0]}),
            ),
        ),
    )

    result = dataset_runtime.publish_parts(
        definition=dispatch_definition,
        target=target,
        parts=(
            RuntimeDatasetPart(
                key=part_002,
                data=pd.DataFrame(
                    {
                        'shift_id': pd.Series(dtype='int64'),
                        'tonnage': pd.Series(dtype='float64'),
                    }
                ),
            ),
        ),
    )
    table = dataset_runtime.read_table(
        definition=dispatch_definition,
        target=target,
    ).table

    assert result.status is PublicationStatus.SKIPPED
    assert table['shift_id'].to_pylist() == [26199001]


def test_empty_parts_request_is_skipped(
    dataset_runtime: DatasetRuntime,
    dispatch_definition: DatasetDefinition,
) -> None:
    target = _dispatch_target(dispatch_definition)

    result = dataset_runtime.publish_parts(
        definition=dispatch_definition,
        target=target,
    )

    assert result.status is PublicationStatus.SKIPPED
