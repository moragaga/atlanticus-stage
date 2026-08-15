import json
import tomllib
from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_process_exposes_entrypoint_and_container_command() -> None:
    project = tomllib.loads((_root() / 'pyproject.toml').read_text(encoding='utf-8'))

    assert (
        project['project']['scripts']['ada-pi-web-api'] == 'ada.processes.pi_web_api.bootstrap:main'
    )
    assert project['tool']['atlanticus']['container']['command'] == 'ada-pi-web-api'
    assert project['tool']['atlanticus']['container']['system-profile'] == 'base'


def test_detail_templates_exist_without_real_local_env() -> None:
    root = _root()

    assert (root / '.env.detail').is_file()
    assert not (root / '.env').exists()
    assert json.loads((root / 'config.detail.json').read_text(encoding='utf-8')) == {}
    secrets = json.loads((root / 'secrets.detail.json').read_text(encoding='utf-8'))
    variables = {item['var_name'] for item in secrets}
    assert {'COMPANY_ABREV', 'PRODUCT_ABREV'} <= variables
    assert {'PI_WEB_API_USERNAME', 'PI_WEB_API_PASSWORD'} <= variables


def test_validation_gate_formats_source_and_runs_tests_explicitly() -> None:
    root = _root()
    shell = (root / 'scripts' / 'check.sh').read_text(encoding='utf-8')
    batch = (root / 'scripts' / 'check.bat').read_text(encoding='utf-8')

    for script in (shell, batch):
        assert 'scopes/ada/scripts/processes/process_bundle.py' in script.replace('\\', '/')
        assert 'ruff check --fix --exit-zero' in script
        assert 'ruff format' in script
        assert 'ruff format --check .' in script
        assert 'python -m pytest -ra tests' in script
        assert 'Ruff lint' in script
        assert 'Pytest' in script
