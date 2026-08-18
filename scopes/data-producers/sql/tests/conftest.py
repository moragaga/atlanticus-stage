from __future__ import annotations

import pytest

from atlanticus.data_producers.sql import (
    DataValueKind,
    SqlColumnDefinition,
    SqlLoadStrategy,
    SqlSourceDefinition,
    SqlStorageMode,
)


@pytest.fixture
def scoped_definition() -> SqlSourceDefinition:
    return SqlSourceDefinition(
        source_key='source_scoped',
        source_table='dbo.source_scoped',
        storage_mode=SqlStorageMode.PARTITIONED,
        load_strategy=SqlLoadStrategy.SCOPED,
        scope_column='ScopeId',
        scope_output_column='scope_id',
        materialization_name='window',
        partition_dimensions=('year', 'window'),
        source_last_update_output_column='moment',
        columns=(
            SqlColumnDefinition(
                source_name='ScopeId',
                output_name='scope_id',
                value_kind=DataValueKind.INTEGER,
                required=True,
            ),
            SqlColumnDefinition(
                source_name='Moment',
                output_name='moment',
                value_kind=DataValueKind.DATETIME,
                source_timezone='UTC',
            ),
            SqlColumnDefinition(
                source_name='Value',
                output_name='value',
                value_kind=DataValueKind.FLOAT,
            ),
        ),
    )


@pytest.fixture
def snapshot_definition() -> SqlSourceDefinition:
    return SqlSourceDefinition(
        source_key='source_latest',
        source_table='dbo.source_latest',
        storage_mode=SqlStorageMode.LATEST,
        load_strategy=SqlLoadStrategy.FULL_SNAPSHOT,
        columns=(
            SqlColumnDefinition(
                source_name='Id',
                output_name='id',
                value_kind=DataValueKind.INTEGER,
                required=True,
            ),
        ),
    )
