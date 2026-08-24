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
