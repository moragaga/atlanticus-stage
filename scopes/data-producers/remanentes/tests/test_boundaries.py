from __future__ import annotations

import ast
from pathlib import Path


def test_remanentes_data_producer_does_not_import_ada() -> None:
    root = Path(__file__).resolve().parents[1] / 'src'
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith('ada') for alias in node.names), path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith('ada'), path
