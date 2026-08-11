from __future__ import annotations

import pytest

from atlanticus.datasets import (
    DatasetDefinition,
    DatasetDefinitionError,
    DatasetKey,
    FileSetLayout,
    MaterializationDefinition,
    SingleArtifactLayout,
)


def test_dataset_key_is_logical_and_format_neutral() -> None:
    key = DatasetKey(namespace=('dispatch',), name='truck-events')

    assert key.identifier == 'dispatch/truck-events'


@pytest.mark.parametrize(
    ('namespace', 'name'),
    [
        ((), 'truck-events'),
        ('ingestion', 'truck-events'),
        (('ingestion', '..'), 'truck-events'),
        (('ingestion/dispatch',), 'truck-events'),
        (('ingestion',), 'truck events'),
    ],
)
def test_dataset_key_rejects_ambiguous_or_unsafe_identity(
    namespace: tuple[str, ...],
    name: str,
) -> None:
    with pytest.raises(DatasetDefinitionError):
        DatasetKey(namespace=namespace, name=name)


def test_definition_accepts_single_artifact_and_file_set_materializations() -> None:
    definition = DatasetDefinition(
        key=DatasetKey(namespace=('dispatch',), name='truck-events'),
        materializations=(
            MaterializationDefinition(name='current', layout=SingleArtifactLayout()),
            MaterializationDefinition(
                name='operational-week',
                layout=FileSetLayout(part_dimension='shift_id'),
                partition_dimensions=('operational_week',),
            ),
        ),
    )

    assert definition.get_materialization('current').partition_dimensions == ()
    assert isinstance(definition.get_materialization('operational-week').layout, FileSetLayout)


def test_definition_rejects_duplicate_materializations() -> None:
    current = MaterializationDefinition(name='current', layout=SingleArtifactLayout())

    with pytest.raises(DatasetDefinitionError):
        DatasetDefinition(
            key=DatasetKey(namespace=('pi', 'pi-web-api', 'interpolated'), name='process'),
            materializations=(current, current),
        )


def test_materialization_rejects_duplicate_or_overlapping_dimensions() -> None:
    with pytest.raises(DatasetDefinitionError):
        MaterializationDefinition(
            name='operational-day',
            layout=SingleArtifactLayout(),
            partition_dimensions=('operational_date', 'operational_date'),
        )

    with pytest.raises(DatasetDefinitionError):
        MaterializationDefinition(
            name='operational-week',
            layout=FileSetLayout(part_dimension='shift_id'),
            partition_dimensions=('shift_id',),
        )


def test_definition_rejects_duplicate_and_unsafe_routes() -> None:
    with pytest.raises(DatasetDefinitionError):
        DatasetDefinition(
            key=DatasetKey(namespace=('dispatch',), name='truck-events'),
            materializations=(
                MaterializationDefinition(
                    name='first',
                    layout=SingleArtifactLayout(),
                    route_segments=(),
                ),
                MaterializationDefinition(
                    name='second',
                    layout=SingleArtifactLayout(),
                    route_segments=(),
                ),
            ),
        )

    with pytest.raises(DatasetDefinitionError):
        DatasetDefinition(
            key=DatasetKey(namespace=('dispatch',), name='truck-events'),
            materializations=(
                MaterializationDefinition(name='latest', layout=SingleArtifactLayout()),
            ),
            route_segments=('dispatch', '..'),
        )
