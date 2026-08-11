from __future__ import annotations

import pytest

from atlanticus.datasets import (
    DatasetDefinition,
    DatasetKey,
    DatasetPartition,
    DatasetTarget,
    DatasetTargetError,
    FileSetLayout,
    MaterializationDefinition,
    SingleArtifactLayout,
)


@pytest.fixture
def dispatch_definition() -> DatasetDefinition:
    return DatasetDefinition(
        key=DatasetKey(namespace=('dispatch',), name='truck-events'),
        materializations=(
            MaterializationDefinition(name='current', layout=SingleArtifactLayout()),
            MaterializationDefinition(
                name='operational-week',
                layout=FileSetLayout(part_dimension='shift_id'),
                partition_dimensions=('operational_year', 'operational_week'),
            ),
        ),
    )


def test_target_builds_an_ordered_logical_address(
    dispatch_definition: DatasetDefinition,
) -> None:
    target = dispatch_definition.resolve_target(
        materialization='operational-week',
        partition={
            'operational_week': 'W30',
            'operational_year': '2026',
        },
    )

    assert target.logical_segments == (
        'datasets',
        'dispatch',
        'truck-events',
        'operational-week',
        'operational_year=2026',
        'operational_week=W30',
    )
    assert target.partition is not None
    assert target.partition.as_dict() == {
        'operational_year': '2026',
        'operational_week': 'W30',
    }


def test_snapshot_rejects_partition_and_partitioned_target_requires_it(
    dispatch_definition: DatasetDefinition,
) -> None:
    with pytest.raises(DatasetTargetError):
        dispatch_definition.resolve_target(
            materialization='current',
            partition={'operational_date': '2026-07-20'},
        )

    with pytest.raises(DatasetTargetError):
        dispatch_definition.resolve_target(
            materialization='operational-week',
        )


def test_partition_rejects_missing_additional_and_non_string_values(
    dispatch_definition: DatasetDefinition,
) -> None:
    with pytest.raises(DatasetTargetError):
        dispatch_definition.resolve_target(
            materialization='operational-week',
            partition={'operational_year': '2026'},
        )

    with pytest.raises(DatasetTargetError):
        dispatch_definition.resolve_target(
            materialization='operational-week',
            partition={
                'operational_year': '2026',
                'operational_week': 'W30',
                'day': '1',
            },
        )

    with pytest.raises(DatasetTargetError):
        dispatch_definition.resolve_target(
            materialization='operational-week',
            partition={'operational_year': 2026, 'operational_week': 'W30'},  # type: ignore[dict-item]
        )


def test_file_set_part_uses_definition_dimension_but_not_a_physical_filename(
    dispatch_definition: DatasetDefinition,
) -> None:
    target = dispatch_definition.resolve_target(
        materialization='operational-week',
        partition={'operational_year': '2026', 'operational_week': 'W30'},
    )

    part = dispatch_definition.resolve_part(target=target, value='26199001')

    assert part.dimension == 'shift_id'
    assert part.identifier.endswith('#shift_id=26199001')


def test_single_artifact_target_does_not_accept_parts(
    dispatch_definition: DatasetDefinition,
) -> None:
    target = dispatch_definition.resolve_target(
        materialization='current',
    )

    with pytest.raises(DatasetTargetError):
        dispatch_definition.resolve_part(target=target, value='26199001')


def test_definition_rejects_a_target_from_another_dataset(
    dispatch_definition: DatasetDefinition,
) -> None:
    target = DatasetTarget(
        dataset=DatasetKey(namespace=('pi', 'pi-web-api', 'interpolated'), name='process'),
        materialization='current',
    )

    with pytest.raises(DatasetTargetError):
        dispatch_definition.validate_target(target)


def test_partition_is_immutable_and_hashable() -> None:
    partition = DatasetPartition(values=(('operational_date', '2026-07-20'),))

    assert hash(partition)
    assert partition.as_dict() == {'operational_date': '2026-07-20'}


def test_pi_sources_share_the_dataset_root_without_colliding() -> None:
    materialization = MaterializationDefinition(name='latest', layout=SingleArtifactLayout())
    web_api = DatasetDefinition(
        key=DatasetKey(
            namespace=('pi', 'pi-web-api', 'interpolated'),
            name='operational-signals',
        ),
        materializations=(materialization,),
    ).resolve_target(materialization='latest')
    notpii = DatasetDefinition(
        key=DatasetKey(
            namespace=('pi', 'notpii', 'interpolated'),
            name='operational-signals',
        ),
        materializations=(materialization,),
    ).resolve_target(materialization='latest')

    assert web_api.identifier == ('datasets/pi/pi-web-api/interpolated/operational-signals/latest')
    assert notpii.identifier == 'datasets/pi/notpii/interpolated/operational-signals/latest'
    assert web_api != notpii


def test_definition_resolves_custom_route_without_changing_target_identity() -> None:
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

    assert target.identifier == (
        'datasets/dispatch/std_shift_dumps/shift/year=2026/month=08/day=06/turn=001'
    )
    assert definition.resolve_route_segments(target) == (
        'dispatch',
        'std_shift_dumps',
        'year=2026',
        'month=08',
        'day=06',
        'turn=001',
    )


def test_dataset_route_can_be_overridden_explicitly() -> None:
    definition = DatasetDefinition(
        key=DatasetKey(namespace=('source',), name='logical-name'),
        materializations=(MaterializationDefinition(name='latest', layout=SingleArtifactLayout()),),
        route_segments=('dispatch', 'std_truck'),
    )
    target = definition.resolve_target(materialization='latest')

    assert definition.resolve_route_segments(target) == ('dispatch', 'std_truck', 'latest')
