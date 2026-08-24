import ast
from pathlib import Path

_SOURCE_ROOT = Path(__file__).parents[1] / 'src' / 'ada' / 'processes' / 'alarms_runtime'


def test_production_source_contains_no_comments() -> None:
    for path in sorted(_SOURCE_ROOT.glob('*.py')):
        source = path.read_text(encoding='utf-8')
        assert '#' not in source


def test_process_integration_does_not_import_alarm_engine_layers() -> None:
    forbidden = ('ada.alarms.core', 'ada.alarms.evaluation', 'ada.alarms.management')
    for path in sorted(_SOURCE_ROOT.glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith(forbidden)
