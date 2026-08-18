import pytest

from ada.processes.blockgrade.models import (
    BlockgradeColumnDefinition,
    BlockgradeLoadStrategy,
    BlockgradeSourceDefinition,
    BlockgradeStorageMode,
    BlockgradeValueKind,
)


def test_shift_source_requires_shift_column() -> None:
    with pytest.raises(ValueError, match='shift_id_column is required'):
        BlockgradeSourceDefinition(
            source_key='a',
            source_table='dbo.a',
            storage_mode=BlockgradeStorageMode.SHIFT,
            load_strategy=BlockgradeLoadStrategy.SHIFT_WINDOW,
            columns=(BlockgradeColumnDefinition('Id', 'id', BlockgradeValueKind.INTEGER),),
        )


def test_last_update_must_reference_datetime_output(shift_definition) -> None:
    with pytest.raises(ValueError, match='datetime column'):
        BlockgradeSourceDefinition(
            source_key='a',
            source_table='dbo.a',
            storage_mode=BlockgradeStorageMode.LATEST,
            load_strategy=BlockgradeLoadStrategy.FULL_SNAPSHOT,
            source_last_update_output_column='id',
            columns=(BlockgradeColumnDefinition('Id', 'id', BlockgradeValueKind.INTEGER),),
        )


def test_expected_output_columns_follow_catalog_order(shift_definition) -> None:
    assert shift_definition.expected_output_columns == ('shift_id', 'moment', 'value')
