import tomllib
from pathlib import Path


def test_core_is_an_independent_project_under_an_organizer_directory() -> None:
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / 'pyproject.toml').read_text())

    assert project['project']['name'] == 'atlanticus-data-producers-core'
    assert project['project']['dependencies'] == []
    assert not (root.parent / 'pyproject.toml').exists()
