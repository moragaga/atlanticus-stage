import ast
from pathlib import Path


def test_data_producers_do_not_depend_on_ada() -> None:
    source_root = Path(__file__).resolve().parents[1] / 'src' / 'atlanticus' / 'data_producers'

    for path in source_root.rglob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith('ada') for alias in node.names), path
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith('ada'), path
