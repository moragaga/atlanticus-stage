import json
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_process_contract_files_are_present_and_consistent() -> None:
    for name in (
        'FIRST_STEP.txt',
        '.env.detail',
        '.python-version',
        'config.detail.json',
        'secrets.detail.json',
        'pyproject.toml',
    ):
        assert (_ROOT / name).is_file()

    detail = json.loads((_ROOT / 'config.detail.json').read_text(encoding='utf-8'))
    assert detail['configuration']['replicaTimeout'] == 610
    assert detail['template']['container']['name'] == 'kpis-delivery-service'

    env = (_ROOT / '.env.detail').read_text(encoding='utf-8')
    assert 'APPLICATION=ada-operaciones-integradas-local' in env
    assert 'COSMOS_CONSUMPTION_CONTAINER_NAME=application-data' in env
    assert 'KPI_DELIVERY_POLL_INTERVAL_SECONDS=1' in env

    pyproject = tomllib.loads((_ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    assert pyproject['project']['scripts']['ada-kpis-delivery'] == (
        'ada.processes.kpis_delivery.bootstrap:main'
    )
    assert pyproject['tool']['atlanticus']['container'] == {
        'command': 'ada-kpis-delivery',
        'system-profile': 'base',
    }


def test_secrets_detail_declares_consumption_key_as_key_vault_secret() -> None:
    entries = json.loads((_ROOT / 'secrets.detail.json').read_text(encoding='utf-8'))
    by_name = {item['var_name']: item for item in entries}

    cosmos_key = by_name['COSMOS_CONSUMPTION_KEY']
    assert cosmos_key['exists_in_key_vault'] is True
    assert cosmos_key['secret_name'] == 'secret-cosmos-consumption-key'
    assert cosmos_key['value'] is None
