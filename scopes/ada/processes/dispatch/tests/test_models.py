import pytest

from ada.processes.dispatch.models import (
    DispatchColumnDefinition,
    DispatchLoadStrategy,
    DispatchSourceDefinition,
    DispatchStorageMode,
    DispatchValueKind,
)


def test_shift_source_requires_shift_column() -> None:
    with pytest.raises(ValueError, match='shift_id_column is required'):
        DispatchSourceDefinition(
            source_key='a',
            source_table='dbo.a',
            storage_mode=DispatchStorageMode.SHIFT,
            load_strategy=DispatchLoadStrategy.SHIFT_WINDOW,
            columns=(DispatchColumnDefinition('Id', 'id', DispatchValueKind.INTEGER),),
        )


def test_last_update_must_reference_datetime_output(shift_definition) -> None:
    with pytest.raises(ValueError, match='datetime column'):
        DispatchSourceDefinition(
            source_key='a',
            source_table='dbo.a',
            storage_mode=DispatchStorageMode.LATEST,
            load_strategy=DispatchLoadStrategy.FULL_SNAPSHOT,
            source_last_update_output_column='id',
            columns=(DispatchColumnDefinition('Id', 'id', DispatchValueKind.INTEGER),),
        )


def test_expected_output_columns_follow_catalog_order(shift_definition) -> None:
    assert shift_definition.expected_output_columns == ('shift_id', 'moment', 'value')
