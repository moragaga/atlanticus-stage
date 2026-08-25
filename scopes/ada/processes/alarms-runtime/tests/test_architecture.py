import ast
from pathlib import Path

_ROOT = Path(__file__).parents[1]
_SOURCE_ROOT = _ROOT / 'src' / 'ada' / 'processes' / 'alarms_runtime'
_CORE_ROOT = _ROOT.parents[1] / 'alarms' / 'core' / 'src' / 'ada' / 'alarms' / 'core'
_PERSISTENCE_ROOT = (
    _ROOT.parents[1] / 'alarms' / 'persistence' / 'src' / 'ada' / 'alarms' / 'persistence'
)


def test_production_source_contains_no_comments() -> None:
    for path in sorted(_SOURCE_ROOT.glob('*.py')):
        source = path.read_text(encoding='utf-8')
        assert '#' not in source


def test_process_composition_does_not_import_concrete_alarm_engine_subsystems() -> None:
    forbidden = ('ada.alarms.evaluation', 'ada.alarms.management')
    for path in sorted(_SOURCE_ROOT.glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith(forbidden)


def test_execution_session_uses_shared_planner_without_source_io() -> None:
    session_path = _SOURCE_ROOT / 'session.py'
    forbidden = (
        'ada.data.sources',
        'atlanticus.datasets',
        'atlanticus.runtime',
        'pandas',
        'pyarrow',
    )
    tree = ast.parse(session_path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert not node.module.startswith(forbidden)
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith(forbidden) for alias in node.names)


def test_iteration_layer_uses_shared_sources_without_physical_dataset_clients() -> None:
    iteration_path = _SOURCE_ROOT / 'iteration.py'
    forbidden = ('atlanticus.datasets', 'atlanticus.runtime', 'pandas', 'pyarrow')
    tree = ast.parse(iteration_path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert not node.module.startswith(forbidden)
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith(forbidden) for alias in node.names)


def test_iteration_layer_has_no_internal_wall_clock_or_loaded_data_cache() -> None:
    iteration_path = _SOURCE_ROOT / 'iteration.py'
    tree = ast.parse(iteration_path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {'now', 'utcnow'}
    source = iteration_path.read_text(encoding='utf-8')
    assert '_cached' not in source
    assert 'last_loaded' not in source


def test_operational_cycle_orchestrates_without_physical_dataset_clients_or_wall_clock() -> None:
    cycle_path = _SOURCE_ROOT / 'cycle.py'
    forbidden = ('atlanticus.datasets', 'pandas', 'pyarrow')
    tree = ast.parse(cycle_path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert not node.module.startswith(forbidden)
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith(forbidden) for alias in node.names)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {'now', 'utcnow'}


def test_cycle_integrates_operational_inputs_without_reclassifying_configuration() -> None:
    source = (_SOURCE_ROOT / 'cycle.py').read_text(encoding='utf-8')

    assert 'management_actions=' in source
    assert 'deactivation_decisions=' in source
    assert 'configuration_closures=' not in source
    assert 'ConfigurationAdoptionPlan' not in source
    assert 'plan_configuration_adoption' not in source


def test_configuration_adoption_contract_has_no_io_or_durability_dependencies() -> None:
    adoption_path = _SOURCE_ROOT / 'adoption.py'
    forbidden = (
        'azure',
        'atlanticus.state',
        'atlanticus.storage',
        'ada.alarms.persistence',
    )
    tree = ast.parse(adoption_path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert not node.module.startswith(forbidden)
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith(forbidden) for alias in node.names)


def test_configuration_adoption_execution_uses_contracts_without_physical_configuration_io() -> (
    None
):
    adoption_path = _SOURCE_ROOT / 'adoption_execution.py'
    forbidden = (
        'azure',
        'atlanticus.storage',
        'ada.processes.alarms_management',
    )
    tree = ast.parse(adoption_path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert not node.module.startswith(forbidden)
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith(forbidden) for alias in node.names)


def test_input_contract_and_consumer_do_not_import_physical_management_storage() -> None:
    forbidden = ('azure', 'atlanticus.storage', 'ada.processes.alarms_management')
    for name in ('inputs.py', 'consumer.py'):
        tree = ast.parse((_SOURCE_ROOT / name).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith(forbidden)
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith(forbidden) for alias in node.names)


def test_low_level_durability_remains_core_agnostic() -> None:
    _assert_file_does_not_import(_SOURCE_ROOT / 'durability.py', 'ada.alarms.core')


def test_core_and_persistence_remain_independent() -> None:
    _assert_tree_does_not_import(_CORE_ROOT, 'ada.alarms.persistence')
    _assert_tree_does_not_import(_PERSISTENCE_ROOT, 'ada.alarms.core')


def _assert_tree_does_not_import(root: Path, forbidden: str) -> None:
    for path in sorted(root.glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith(forbidden)
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith(forbidden) for alias in node.names)


def _assert_file_does_not_import(path: Path, forbidden: str) -> None:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert not node.module.startswith(forbidden)
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith(forbidden) for alias in node.names)
