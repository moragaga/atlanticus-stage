from pathlib import Path


def test_transport_contract_files_exist() -> None:
    root = Path(__file__).resolve().parents[1]

    for name in (
        '.python-version',
        '.env.detail',
        'FIRST_STEP.txt',
        'config.detail.json',
        'secrets.detail.json',
        'pyproject.toml',
    ):
        assert (root / name).is_file(), name
    assert (root / 'scripts' / 'check.sh').is_file()
    assert (root / 'scripts' / 'check.bat').is_file()


def test_artifact_gate_removes_generated_egg_info() -> None:
    root = Path(__file__).resolve().parents[1]
    shell = (root / 'scripts' / 'check.sh').read_text()
    batch = (root / 'scripts' / 'check.bat').read_text()

    assert "-name '*.egg-info'" in shell
    assert '*.egg-info' in batch


def test_data_producer_dependencies_are_split_by_capability() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / 'pyproject.toml').read_text())
    dependencies = project['project']['dependencies']
    sources = project['tool']['uv']['sources']

    assert 'atlanticus-data-producers-core==0.1.0' in dependencies
    assert 'atlanticus-data-producers-sql==0.1.0' in dependencies
    assert 'atlanticus-data-producers==0.1.0' not in dependencies
    assert sources['atlanticus-data-producers-core']['path'] == '../../../data-producers/core'
    assert sources['atlanticus-data-producers-sql']['path'] == '../../../data-producers/sql'
    assert 'atlanticus-data-producers' not in sources
