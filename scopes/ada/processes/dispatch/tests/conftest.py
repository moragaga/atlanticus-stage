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
def shift_definition() -> SqlSourceDefinition:
    return SqlSourceDefinition(
        source_key='source_shift',
        source_table='dbo.source_shift',
        storage_mode=SqlStorageMode.PARTITIONED,
        load_strategy=SqlLoadStrategy.SCOPED,
        scope_column='ShiftId',
        scope_output_column='shift_id',
        materialization_name='shift',
        partition_dimensions=('year', 'month', 'day', 'turn'),
        source_last_update_output_column='moment',
        enabled=True,
        columns=(
            SqlColumnDefinition(
                source_name='ShiftId',
                output_name='shift_id',
                value_kind=DataValueKind.INTEGER,
                required=True,
            ),
            SqlColumnDefinition(
                source_name='Moment',
                output_name='moment',
                value_kind=DataValueKind.DATETIME,
                source_timezone='America/Santiago',
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
        materialization_name='latest',
        enabled=True,
        columns=(
            SqlColumnDefinition(
                source_name='Id',
                output_name='id',
                value_kind=DataValueKind.INTEGER,
                required=True,
            ),
            SqlColumnDefinition(
                source_name='Name',
                output_name='name',
                value_kind=DataValueKind.TEXT,
            ),
        ),
    )
