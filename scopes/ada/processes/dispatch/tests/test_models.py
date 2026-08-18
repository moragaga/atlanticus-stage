import pytest

from atlanticus.data_producers.sql import (
    DataValueKind,
    SqlColumnDefinition,
    SqlLoadStrategy,
    SqlSourceDefinition,
    SqlStorageMode,
)


def test_scoped_source_requires_scope_columns() -> None:
    with pytest.raises(ValueError, match='scope_column and scope_output_column'):
        SqlSourceDefinition(
            source_key='a',
            source_table='dbo.a',
            storage_mode=SqlStorageMode.PARTITIONED,
            load_strategy=SqlLoadStrategy.SCOPED,
            partition_dimensions=('partition',),
            columns=(SqlColumnDefinition('Id', 'id', DataValueKind.INTEGER),),
        )


def test_last_update_must_reference_datetime_output() -> None:
    with pytest.raises(ValueError, match='datetime column'):
        SqlSourceDefinition(
            source_key='a',
            source_table='dbo.a',
            storage_mode=SqlStorageMode.LATEST,
            load_strategy=SqlLoadStrategy.FULL_SNAPSHOT,
            source_last_update_output_column='id',
            columns=(SqlColumnDefinition('Id', 'id', DataValueKind.INTEGER),),
        )


def test_expected_output_columns_follow_catalog_order(shift_definition) -> None:
    assert shift_definition.expected_output_columns == ('shift_id', 'moment', 'value')
