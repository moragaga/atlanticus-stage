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
