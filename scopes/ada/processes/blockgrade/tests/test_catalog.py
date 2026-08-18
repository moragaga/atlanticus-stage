import ast
from pathlib import Path

from ada.processes.blockgrade.catalog import build_catalog
from atlanticus.data_producers.sql import (
    DataValueKind,
    SqlColumnDefinition,
    SqlLoadStrategy,
    SqlSourceDefinition,
    SqlStorageMode,
)


def test_catalog_contains_current_blockgrade_source() -> None:
    catalog = build_catalog()

    assert tuple(item.source_key for item in catalog) == ('mms_blockgrade_details_bucket',)


def test_blockgrade_source_declares_real_shift_contract() -> None:
    definition = build_catalog()[0]

    assert definition.source_table == 'dbo.mms_blockgradedetailsbucket'
    assert definition.storage_mode is SqlStorageMode.PARTITIONED
    assert definition.load_strategy is SqlLoadStrategy.SCOPED
    assert definition.scope_column == 'shiftindex'
    assert definition.scope_output_column == 'shift_id'
    assert definition.materialization_name == 'shift'
    assert definition.partition_dimensions == ('year', 'month', 'day', 'turn')
    assert definition.source_last_update_output_column is None
    assert len(definition.columns) == 86
    assert definition.required_output_columns == ('shift_id',)


def test_blockgrade_catalog_preserves_expected_edge_column_mappings() -> None:
    definition = build_catalog()[0]
    by_source = {item.source_name: item.output_name for item in definition.columns}

    assert by_source['shiftindex'] == 'shift_id'
    assert by_source['XY'] == 'xy'
    assert by_source['Hra_InicioCarga'] == 'hra_inicio_carga'
    assert by_source['UbiDescarga'] == 'ubi_descarga'
    assert by_source['_as'] == 'as'
    assert by_source['banco'] == 'banco'


def test_catalog_excludes_disabled_sources(monkeypatch) -> None:
    from ada.processes.blockgrade.catalog import provider

    column = SqlColumnDefinition(
        source_name='ShiftId',
        output_name='shift_id',
        value_kind=DataValueKind.INTEGER,
        required=True,
    )
    enabled = SqlSourceDefinition(
        source_key='enabled_source',
        source_table='dbo.enabled_source',
        storage_mode=SqlStorageMode.PARTITIONED,
        load_strategy=SqlLoadStrategy.SCOPED,
        columns=(column,),
        enabled=True,
        scope_column='ShiftId',
        scope_output_column='shift_id',
        materialization_name='shift',
        partition_dimensions=('year', 'month', 'day', 'turn'),
    )
    disabled = SqlSourceDefinition(
        source_key='disabled_source',
        source_table='dbo.disabled_source',
        storage_mode=SqlStorageMode.PARTITIONED,
        load_strategy=SqlLoadStrategy.SCOPED,
        columns=(column,),
        enabled=False,
        scope_column='ShiftId',
        scope_output_column='shift_id',
        materialization_name='shift',
        partition_dimensions=('year', 'month', 'day', 'turn'),
    )
    monkeypatch.setattr(provider, 'DEFINITIONS', (enabled, disabled))

    assert provider.build_catalog() == (enabled,)


def test_table_catalog_uses_explicit_named_column_parameters() -> None:
    tables_root = (
        Path(__file__).parents[1]
        / 'src'
        / 'ada'
        / 'processes'
        / 'blockgrade'
        / 'catalog'
        / 'tables'
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
