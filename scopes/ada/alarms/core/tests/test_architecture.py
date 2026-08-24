import ast
from pathlib import Path

_PRODUCTION_ROOT = Path('src/ada/alarms/core')


def test_core_has_no_runtime_persistence_or_connectivity_imports() -> None:
    forbidden = (
        'ada.alarms.persistence',
        'ada.processes',
        'atlanticus',
    )
    for path in sorted(_PRODUCTION_ROOT.glob('*.py')):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '']
            else:
                continue
            assert not any(name.startswith(forbidden) for name in names)


def test_production_code_has_no_comments() -> None:
    for path in sorted(_PRODUCTION_ROOT.glob('*.py')):
        for line in path.read_text().splitlines():
            assert not line.lstrip().startswith('#')
