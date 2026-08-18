import ast
from pathlib import Path

from ada.processes.dispatch.catalog import build_catalog
from atlanticus.data_producers.sql import (
    DataValueKind,
    SqlColumnDefinition,
    SqlLoadStrategy,
    SqlSourceDefinition,
    SqlStorageMode,
)


def test_catalog_contains_current_dispatch_sources_in_stable_order() -> None:
    catalog = build_catalog()

    assert tuple(item.source_key for item in catalog) == (
        'tiempos_mlp',
        'std_shift_state',
        'std_shift_loads',
        'std_shift_dumps',
        'std_shift_grade',
        'std_shift_loads_2',
        'std_truck',
    )


def test_tiempos_mlp_declares_functional_last_update() -> None:
    definition = build_catalog()[0]

    assert definition.source_table == 'dbo.tiempos_mlp'
    assert definition.storage_mode is SqlStorageMode.PARTITIONED
    assert definition.load_strategy is SqlLoadStrategy.SCOPED
    assert definition.scope_column == 'ShiftId'
    assert definition.scope_output_column == 'shift_id'
    assert definition.materialization_name == 'shift'
    assert definition.partition_dimensions == ('year', 'month', 'day', 'turn')
    assert definition.source_last_update_output_column == 'moment'


def test_all_shift_sources_share_partition_contract() -> None:
    definitions = tuple(item for item in build_catalog() if item.source_key != 'std_truck')

    assert len(definitions) == 6
    assert all(item.storage_mode is SqlStorageMode.PARTITIONED for item in definitions)
    assert all(item.load_strategy is SqlLoadStrategy.SCOPED for item in definitions)
    assert all(item.scope_column == 'ShiftId' for item in definitions)
    assert all(item.scope_output_column == 'shift_id' for item in definitions)
    assert all(item.materialization_name == 'shift' for item in definitions)
    assert all(
        item.partition_dimensions == ('year', 'month', 'day', 'turn') for item in definitions
    )


def test_std_truck_is_latest_full_snapshot() -> None:
    definition = next(item for item in build_catalog() if item.source_key == 'std_truck')

    assert definition.storage_mode is SqlStorageMode.LATEST
    assert definition.load_strategy is SqlLoadStrategy.FULL_SNAPSHOT
    assert definition.scope_column is None
    assert definition.scope_output_column is None
    assert definition.materialization_name == 'latest'
    assert definition.partition_dimensions == ()


def test_catalog_excludes_disabled_sources(monkeypatch) -> None:
    from ada.processes.dispatch.catalog import provider

    column = SqlColumnDefinition(
        source_name='Id',
        output_name='id',
        value_kind=DataValueKind.INTEGER,
        required=True,
    )
    enabled = SqlSourceDefinition(
        source_key='enabled_source',
        source_table='dbo.enabled_source',
        storage_mode=SqlStorageMode.LATEST,
        load_strategy=SqlLoadStrategy.FULL_SNAPSHOT,
        columns=(column,),
        enabled=True,
    )
    disabled = SqlSourceDefinition(
        source_key='disabled_source',
        source_table='dbo.disabled_source',
        storage_mode=SqlStorageMode.LATEST,
        load_strategy=SqlLoadStrategy.FULL_SNAPSHOT,
        columns=(column,),
        enabled=False,
    )
    monkeypatch.setattr(provider, 'DEFINITIONS', (enabled, disabled))

    assert provider.build_catalog() == (enabled,)


def test_table_catalog_uses_explicit_named_column_parameters() -> None:
    tables_root = (
        Path(__file__).parents[1] / 'src' / 'ada' / 'processes' / 'dispatch' / 'catalog' / 'tables'
    )
    expected_column_keywords = {'source_name', 'output_name', 'value_kind', 'required'}

    for path in tables_root.glob('*.py'):
        if path.name == '__init__.py':
            continue
        tree = ast.parse(path.read_text())
        source_definitions = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == 'column':
                assert not node.args, path.name
                assert {item.arg for item in node.keywords} == expected_column_keywords, path.name
            if isinstance(node.func, ast.Name) and node.func.id == 'SqlSourceDefinition':
                source_definitions += 1
                assert not node.args, path.name
                assert 'enabled' in {item.arg for item in node.keywords}, path.name
        assert source_definitions == 1, path.name
