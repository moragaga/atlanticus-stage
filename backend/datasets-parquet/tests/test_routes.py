from __future__ import annotations

from pathlib import Path

from atlanticus.datasets import (
    DatasetDefinition,
    DatasetKey,
    MaterializationDefinition,
    SingleArtifactLayout,
)
from atlanticus.datasets.parquet import ParquetDatasetStore


def test_store_uses_definition_route_instead_of_target_identity(tmp_path: Path) -> None:
    definition = DatasetDefinition(
        key=DatasetKey(namespace=('dispatch',), name='std_shift_dumps'),
        materializations=(
            MaterializationDefinition(
                name='shift',
                layout=SingleArtifactLayout(),
                partition_dimensions=('year', 'month', 'day', 'turn'),
                route_segments=(),
            ),
        ),
    )
    target = definition.resolve_target(
        materialization='shift',
        partition={'year': '2026', 'month': '08', 'day': '06', 'turn': '001'},
    )
    store = ParquetDatasetStore(root=tmp_path / 'datasets')

    assert store.path_for(definition=definition, target=target) == (
        tmp_path / 'datasets/dispatch/std_shift_dumps/year=2026/month=08/day=06/turn=001'
    )
