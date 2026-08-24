from __future__ import annotations

import ast
from pathlib import Path


def test_data_core_has_no_runtime_or_infrastructure_dependencies() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / 'pyproject.toml').read_text(encoding='utf-8')
    assert 'dependencies = []' in pyproject

    forbidden_roots = {'atlanticus', 'azure', 'cosmos', 'flask', 'pandas', 'pyarrow'}
    for path in (root / 'src').rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split('.')[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = {node.module.split('.')[0]}
            else:
                continue
            assert not names & forbidden_roots, (
                f'{path}: forbidden imports {names & forbidden_roots}'
            )
