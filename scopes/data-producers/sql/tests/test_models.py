import pytest

from atlanticus.data_producers.sql import (
    DataValueKind,
    SqlColumnDefinition,
    SqlLoadStrategy,
    SqlSourceDefinition,
    SqlStorageMode,
)


def test_partitioned_source_requires_generic_scope_contract() -> None:
    with pytest.raises(ValueError, match='scope_column and scope_output_column'):
        SqlSourceDefinition(
            source_key='a',
            source_table='dbo.a',
            storage_mode=SqlStorageMode.PARTITIONED,
            load_strategy=SqlLoadStrategy.SCOPED,
            partition_dimensions=('part',),
            columns=(SqlColumnDefinition('ScopeId', 'scope_id', DataValueKind.INTEGER),),
        )


def test_partitioned_source_requires_partition_dimensions() -> None:
    with pytest.raises(ValueError, match='partition_dimensions'):
        SqlSourceDefinition(
            source_key='a',
            source_table='dbo.a',
            storage_mode=SqlStorageMode.PARTITIONED,
            load_strategy=SqlLoadStrategy.SCOPED,
            scope_column='ScopeId',
            scope_output_column='scope_id',
            columns=(SqlColumnDefinition('ScopeId', 'scope_id', DataValueKind.INTEGER),),
        )


def test_latest_source_rejects_scope_contract() -> None:
    with pytest.raises(ValueError, match='omit scope columns'):
        SqlSourceDefinition(
            source_key='a',
            source_table='dbo.a',
            storage_mode=SqlStorageMode.LATEST,
            load_strategy=SqlLoadStrategy.FULL_SNAPSHOT,
            scope_column='ScopeId',
            scope_output_column='scope_id',
            columns=(SqlColumnDefinition('ScopeId', 'scope_id', DataValueKind.INTEGER),),
        )
