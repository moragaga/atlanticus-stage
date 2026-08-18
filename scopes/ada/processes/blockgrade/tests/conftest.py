from __future__ import annotations

import pytest

from ada.processes.blockgrade.models import (
    BlockgradeColumnDefinition,
    BlockgradeLoadStrategy,
    BlockgradeSourceDefinition,
    BlockgradeStorageMode,
    BlockgradeValueKind,
)


@pytest.fixture
def shift_definition() -> BlockgradeSourceDefinition:
    return BlockgradeSourceDefinition(
        source_key='source_shift',
        source_table='dbo.source_shift',
        storage_mode=BlockgradeStorageMode.SHIFT,
        load_strategy=BlockgradeLoadStrategy.SHIFT_WINDOW,
        shift_id_column='ShiftId',
        source_last_update_output_column='moment',
        enabled=True,
        columns=(
            BlockgradeColumnDefinition(
                source_name='ShiftId',
                output_name='shift_id',
                value_kind=BlockgradeValueKind.INTEGER,
                required=True,
            ),
            BlockgradeColumnDefinition(
                source_name='Moment',
                output_name='moment',
                value_kind=BlockgradeValueKind.DATETIME,
                source_timezone='America/Santiago',
            ),
            BlockgradeColumnDefinition(
                source_name='Value',
                output_name='value',
                value_kind=BlockgradeValueKind.FLOAT,
            ),
        ),
    )


@pytest.fixture
def snapshot_definition() -> BlockgradeSourceDefinition:
    return BlockgradeSourceDefinition(
        source_key='source_latest',
        source_table='dbo.source_latest',
        storage_mode=BlockgradeStorageMode.LATEST,
        load_strategy=BlockgradeLoadStrategy.FULL_SNAPSHOT,
        enabled=True,
        columns=(
            BlockgradeColumnDefinition(
                source_name='Id',
                output_name='id',
                value_kind=BlockgradeValueKind.INTEGER,
                required=True,
            ),
            BlockgradeColumnDefinition(
                source_name='Name',
                output_name='name',
                value_kind=BlockgradeValueKind.TEXT,
            ),
        ),
    )
