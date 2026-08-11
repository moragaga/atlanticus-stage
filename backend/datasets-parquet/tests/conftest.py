from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlanticus.datasets import (
    DatasetDefinition,
    DatasetKey,
    FileSetLayout,
    MaterializationDefinition,
    SingleArtifactLayout,
)


@pytest.fixture
def clock() -> datetime:
    return datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def pi_definition() -> DatasetDefinition:
    return DatasetDefinition(
        key=DatasetKey(namespace=('pi', 'pi-web-api', 'recorded'), name='process'),
        materializations=(
            MaterializationDefinition(
                name='granular',
                layout=SingleArtifactLayout(),
                partition_dimensions=('year', 'month', 'day'),
            ),
        ),
    )


@pytest.fixture
def dispatch_definition() -> DatasetDefinition:
    return DatasetDefinition(
        key=DatasetKey(namespace=('dispatch',), name='shift-dumps'),
        materializations=(
            MaterializationDefinition(
                name='operational-day',
                layout=FileSetLayout(part_dimension='shift_id'),
                partition_dimensions=('year', 'month', 'day'),
            ),
        ),
    )
