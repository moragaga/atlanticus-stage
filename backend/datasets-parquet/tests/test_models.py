from __future__ import annotations

import pyarrow as pa

from atlanticus.datasets import (
    DatasetDefinition,
    DatasetKey,
    MaterializationDefinition,
    SingleArtifactLayout,
)
from atlanticus.datasets.parquet import ParquetReadResult


def test_read_result_normalizes_collection_inputs_to_immutable_tuples() -> None:
    definition = DatasetDefinition(
        key=DatasetKey(namespace=('test',), name='dataset'),
        materializations=(
            MaterializationDefinition(
                name='daily',
                layout=SingleArtifactLayout(),
                partition_dimensions=('day',),
            ),
        ),
    )
    target = definition.resolve_target(
        materialization='daily',
        partition={'day': '2026-08-12'},
    )

    result = ParquetReadResult(
        table=pa.table({'value': [1]}),
        targets=[target],
        artifact_count=1,
        size_bytes=1,
        publication_tokens=['token-1'],
        warnings=['warning-1'],
    )

    assert result.targets == (target,)
    assert result.publication_tokens == ('token-1',)
    assert result.warnings == ('warning-1',)
    assert isinstance(result.targets, tuple)
    assert isinstance(result.publication_tokens, tuple)
    assert isinstance(result.warnings, tuple)
