import ast
from pathlib import Path


def test_core_does_not_depend_on_ada_or_technology_specific_producers() -> None:
    source_root = (
        Path(__file__).resolve().parents[1] / 'src' / 'atlanticus' / 'data_producers' / 'core'
    )

    for path in source_root.rglob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith('ada'), path
                    assert not alias.name.startswith('atlanticus.data_producers.sql'), path
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith('ada'), path
                assert not node.module.startswith('atlanticus.data_producers.sql'), path
