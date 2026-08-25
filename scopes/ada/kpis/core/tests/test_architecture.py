from __future__ import annotations

import ast
from pathlib import Path


def test_core_only_depends_on_shared_data_contracts() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / 'pyproject.toml').read_text(encoding='utf-8')
    assert 'ada-operational-data-core==0.1.0' in pyproject
    assert 'ada-operational-data-planner' not in pyproject
    assert 'ada-operational-data-sources' not in pyproject

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


def test_legacy_shared_data_modules_are_removed_from_kpi_core() -> None:
    root = Path(__file__).resolve().parents[1]
    package = root / 'src' / 'ada' / 'kpis' / 'core'
    assert not (package / 'requirements.py').exists()
    assert not (package / 'runtime.py').exists()
