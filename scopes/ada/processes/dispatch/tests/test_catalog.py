from ada.processes.dispatch.catalog import build_catalog
from ada.processes.dispatch.models import DispatchLoadStrategy, DispatchStorageMode


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
    assert definition.storage_mode is DispatchStorageMode.SHIFT
    assert definition.load_strategy is DispatchLoadStrategy.SHIFT_WINDOW
    assert definition.shift_id_column == 'ShiftId'
    assert definition.source_last_update_output_column == 'moment'


def test_std_truck_is_latest_full_snapshot() -> None:
    definition = next(item for item in build_catalog() if item.source_key == 'std_truck')

    assert definition.storage_mode is DispatchStorageMode.LATEST
    assert definition.load_strategy is DispatchLoadStrategy.FULL_SNAPSHOT
    assert definition.shift_id_column is None


def test_catalog_excludes_disabled_sources(monkeypatch) -> None:
    from ada.processes.dispatch.catalog import provider
    from ada.processes.dispatch.models import (
        DispatchColumnDefinition,
        DispatchLoadStrategy,
        DispatchSourceDefinition,
        DispatchStorageMode,
        DispatchValueKind,
    )

    enabled = DispatchSourceDefinition(
        source_key='enabled_source',
        source_table='dbo.enabled_source',
        storage_mode=DispatchStorageMode.LATEST,
        load_strategy=DispatchLoadStrategy.FULL_SNAPSHOT,
        columns=(
            DispatchColumnDefinition(
                source_name='Id',
                output_name='id',
                value_kind=DispatchValueKind.INTEGER,
                required=True,
            ),
        ),
        enabled=True,
    )
    disabled = DispatchSourceDefinition(
        source_key='disabled_source',
        source_table='dbo.disabled_source',
        storage_mode=DispatchStorageMode.LATEST,
        load_strategy=DispatchLoadStrategy.FULL_SNAPSHOT,
        columns=(
            DispatchColumnDefinition(
                source_name='Id',
                output_name='id',
                value_kind=DispatchValueKind.INTEGER,
                required=True,
            ),
        ),
        enabled=False,
    )
    monkeypatch.setattr(provider, 'DEFINITIONS', (enabled, disabled))

    assert provider.build_catalog() == (enabled,)


def test_table_catalog_uses_explicit_named_column_parameters() -> None:
    import ast
    from pathlib import Path

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
            if isinstance(node.func, ast.Name) and node.func.id == 'DispatchSourceDefinition':
                source_definitions += 1
                assert not node.args, path.name
                assert 'enabled' in {item.arg for item in node.keywords}, path.name
        assert source_definitions == 1, path.name
