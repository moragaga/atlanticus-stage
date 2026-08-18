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
    assert 'atlanticus-data-producers' not in sources

    if project['tool']['uv'].get('default-groups') == []:
        core_path = sources['atlanticus-data-producers-core']['path']
        sql_path = sources['atlanticus-data-producers-sql']['path']
        assert core_path.startswith('wheels/atlanticus_data_producers_core-')
        assert core_path.endswith('.whl')
        assert (root / core_path).is_file()
        assert sql_path.startswith('wheels/atlanticus_data_producers_sql-')
        assert sql_path.endswith('.whl')
        assert (root / sql_path).is_file()
    else:
        assert sources['atlanticus-data-producers-core'] == {
            'path': '../../../data-producers/core',
            'editable': True,
        }
        assert sources['atlanticus-data-producers-sql'] == {
            'path': '../../../data-producers/sql',
            'editable': True,
        }
