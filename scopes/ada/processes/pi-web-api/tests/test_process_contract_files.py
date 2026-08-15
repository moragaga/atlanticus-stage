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
    assert 'atlanticus-job-runtime==0.5.0' in project['project']['dependencies']
    assert 'atlanticus-datasets-runtime==0.2.0' in project['project']['dependencies']
    assert 'atlanticus-datasets-parquet==0.2.0' in project['project']['dependencies']


def test_detail_templates_exist_without_real_local_env() -> None:
    root = _root()

    assert (root / '.env.detail').is_file()
    assert not (root / '.env').exists()
    assert json.loads((root / 'config.detail.json').read_text(encoding='utf-8')) == {}
    secrets = json.loads((root / 'secrets.detail.json').read_text(encoding='utf-8'))
    variables = {item['var_name'] for item in secrets}
    assert {'COMPANY_ABREV', 'PRODUCT_ABREV'} <= variables
    assert {'PI_WEB_API_USERNAME', 'PI_WEB_API_PASSWORD'} <= variables
    env_detail = (root / '.env.detail').read_text(encoding='utf-8')
    assert 'PI_WEB_API_LEASE_SMOKE_MODE' not in env_detail
    assert not (root / 'src' / 'ada' / 'processes' / 'pi_web_api' / 'lease_smoke.py').exists()
    assert not (root / 'commented' / 'ada' / 'processes' / 'pi_web_api' / 'lease_smoke.py').exists()
    assert not (root / 'tests' / 'test_lease_smoke.py').exists()
    assert 'PI_WEB_API_MAX_DATA_POINTS=150000' in env_detail
    assert 'PI_WEB_API_INTERPOLATED_MAX_WEB_IDS=200' in env_detail
    assert 'PI_WEB_API_INTERPOLATED_MAX_PARALLEL_REQUESTS=3' in env_detail
    assert 'PI_WEB_API_MAX_RECOVERY_LOOKBACK_SECONDS=3600' in env_detail
    assert 'PI_WEB_API_MAX_RECOVERY_WINDOW_SECONDS=3600' in env_detail
    assert 'PI_WEB_API_MAX_RECOVERY_SECONDS' not in env_detail
    assert 'PI_WEB_API_STRESS_BENCHMARK=false' in env_detail
    assert 'PI_WEB_API_STRESS_LOGICAL_TAGS=1000' in env_detail
    assert 'PI_WEB_API_STRESS_LOOKBACK_HOURS=24' in env_detail
    assert 'PI_WEB_API_STRESS_PHYSICAL_TAG_LIMIT=0' in env_detail
    stress_io = (root / '.env.detail.stress-io').read_text(encoding='utf-8')
    assert 'PI_WEB_API_STRESS_KIND=io' in stress_io
    assert 'PI_WEB_API_STRESS_IO_CHUNK_LIMIT=40' in stress_io
    assert 'PI_WEB_API_STRESS_IO_MAX_WORKERS=3' in stress_io
    assert 'ada-pi-web-api --run-once' in stress_io
    placeholders = {
        line.split('=', 1)[0] for line in stress_io.splitlines() if '=COMPLETAR_' in line
    }
    assert placeholders == {
        'VOLUMEN_PATH',
        'PI_WEB_API_BASE_URL',
        'PI_WEB_API_SERVER',
        'PI_WEB_API_USERNAME',
        'PI_WEB_API_PASSWORD',
    }


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


def test_process_contract_documents_single_writer_and_replay_idempotency() -> None:
    contract = (_root() / 'docs' / 'process-contract.md').read_text(encoding='utf-8')

    assert 'ENVIRONMENT + APPLICATION' in contract
    assert 'cada `process_key` debe ser único' in contract
    assert 'exactamente un productor propietario' in contract
    assert 'lease_wait_seconds = adaptive' in contract
    assert 'replicaTimeout = 610' in contract
    assert 'slot_timestamp_utc' in contract
    assert '(tag_name, native_timestamp_utc)' in contract
    assert 'último valor recibido gana' in contract
    assert 'tres reintentos explícitos con pausas de 2, 3 y 5 segundos' in contract
    assert '`outcome=skipped` y `reason=pi_timeout`' in contract
    assert 'PI_WEB_API_MAX_RECOVERY_LOOKBACK_SECONDS' in contract
    assert 'PI_WEB_API_MAX_RECOVERY_WINDOW_SECONDS' in contract
    assert 'PI_WEB_API_INTERPOLATED_MAX_PARALLEL_REQUESTS=3' in contract
    assert 'slot_commit_latency_seconds' in contract
