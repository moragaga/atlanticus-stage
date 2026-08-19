from __future__ import annotations

import json
from pathlib import Path

REPLICA_TIMEOUT = 300
CRON = '0 */2 * * *'
CONTAINER_NAME = 'fabrica-service'
APPLICATION = 'ada-fabrica'


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _env_detail_keys() -> set[str]:
    keys: set[str] = set()
    for raw_line in (_root() / '.env.detail').read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, _ = line.partition('=')
        assert separator == '=', line
        keys.add(key)
    return keys


def test_platform_config_reference_is_complete_and_formatted() -> None:
    path = _root() / 'config.detail.json'
    text = path.read_text(encoding='utf-8')
    config = json.loads(text)

    assert text == json.dumps(config, indent=2, ensure_ascii=False) + '\n'
    assert config == {
        'configuration': {
            'replicaTimeout': REPLICA_TIMEOUT,
            'replicaRetryLimit': 0,
            'triggerType': 'Schedule',
            'scheduleTriggerConfig': {
                'cronExpression': CRON,
                'parallelism': 1,
            },
        },
        'template': {
            'container': {
                'name': CONTAINER_NAME,
                'resources': {
                    'cpu': 0.5,
                    'memory': '1Gi',
                },
            },
        },
    }


def test_secrets_reference_covers_local_contract_and_azure_observability() -> None:
    path = _root() / 'secrets.detail.json'
    text = path.read_text(encoding='utf-8')
    entries = json.loads(text)

    assert text == json.dumps(entries, indent=2, ensure_ascii=False) + '\n'
    by_variable = {entry['var_name']: entry for entry in entries}
    assert len(by_variable) == len(entries)
    assert set(by_variable) == _env_detail_keys() - {'ENVIRONMENT'}
    assert 'ENVIRONMENT' not in by_variable
    assert by_variable['COMPANY_ABREV'] == {
        'var_name': 'COMPANY_ABREV',
        'secret_name': None,
        'value': 'MLP',
        'exists_in_key_vault': False,
    }
    assert by_variable['PRODUCT_ABREV'] == {
        'var_name': 'PRODUCT_ABREV',
        'secret_name': None,
        'value': 'ADA',
        'exists_in_key_vault': False,
    }
    assert by_variable['APPLICATION']['value'] == APPLICATION
    assert by_variable['VOLUMEN_PATH']['value'] == '/app/volumen'
    assert by_variable['ATLANTICUS_AZURE_OBSERVABILITY_MODE']['value'] == 'export'
    assert by_variable['ATLANTICUS_AZURE_OBSERVABILITY_PROFILE']['value'] == 'slim'
    assert by_variable['APPLICATION_INSIGHTS_CONNECTION_STRING'] == {
        'var_name': 'APPLICATION_INSIGHTS_CONNECTION_STRING',
        'secret_name': 'secret-appi-connection-string',
        'value': None,
        'exists_in_key_vault': True,
    }
