import tomllib
from pathlib import Path


def test_sql_is_an_independent_project_with_explicit_core_dependency() -> None:
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / 'pyproject.toml').read_text())

    assert project['project']['name'] == 'atlanticus-data-producers-sql'
    assert 'atlanticus-data-producers-core==0.1.0' in project['project']['dependencies']
    assert project['tool']['uv']['sources']['atlanticus-data-producers-core']['path'] == '../core'
    assert not (root.parent / 'pyproject.toml').exists()
