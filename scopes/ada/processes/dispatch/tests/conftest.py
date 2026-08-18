from __future__ import annotations

import pytest

from ada.processes.dispatch.models import (
    DispatchColumnDefinition,
    DispatchLoadStrategy,
    DispatchSourceDefinition,
    DispatchStorageMode,
    DispatchValueKind,
)


@pytest.fixture
def shift_definition() -> DispatchSourceDefinition:
    return DispatchSourceDefinition(
        source_key='source_shift',
        source_table='dbo.source_shift',
        storage_mode=DispatchStorageMode.SHIFT,
        load_strategy=DispatchLoadStrategy.SHIFT_WINDOW,
        shift_id_column='ShiftId',
        source_last_update_output_column='moment',
        enabled=True,
        columns=(
            DispatchColumnDefinition(
                source_name='ShiftId',
                output_name='shift_id',
                value_kind=DispatchValueKind.INTEGER,
                required=True,
            ),
            DispatchColumnDefinition(
                source_name='Moment',
                output_name='moment',
                value_kind=DispatchValueKind.DATETIME,
                source_timezone='America/Santiago',
            ),
            DispatchColumnDefinition(
                source_name='Value',
                output_name='value',
                value_kind=DispatchValueKind.FLOAT,
            ),
        ),
    )


@pytest.fixture
def snapshot_definition() -> DispatchSourceDefinition:
    return DispatchSourceDefinition(
        source_key='source_latest',
        source_table='dbo.source_latest',
        storage_mode=DispatchStorageMode.LATEST,
        load_strategy=DispatchLoadStrategy.FULL_SNAPSHOT,
        enabled=True,
        columns=(
            DispatchColumnDefinition(
                source_name='Id',
                output_name='id',
                value_kind=DispatchValueKind.INTEGER,
                required=True,
            ),
            DispatchColumnDefinition(
                source_name='Name',
                output_name='name',
                value_kind=DispatchValueKind.TEXT,
            ),
        ),
    )
