from __future__ import annotations

import json
from pathlib import Path

FLAG = 'ATLANTICUS_OBSERVABILITY_FILE_LOGS_ENABLED'
EXPECTED_ENTRY = {
    'var_name': FLAG,
    'secret_name': None,
    'value': 'true',
    'exists_in_key_vault': False,
}


def _processes_root() -> Path:
    return Path(__file__).resolve().parents[3] / 'processes'


def _env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, _ = line.partition('=')
        assert separator == '=', line
        keys.add(key)
    return keys


def test_all_process_deployment_contracts_include_file_logs_flag() -> None:
    process_roots = tuple(
        sorted(
            path
            for path in _processes_root().iterdir()
            if path.is_dir() and (path / '.env.detail').is_file()
        )
    )

    assert process_roots

    for process_root in process_roots:
        env_path = process_root / '.env.detail'
        secrets_path = process_root / 'secrets.detail.json'
        env_text = env_path.read_text(encoding='utf-8')
        entries = json.loads(secrets_path.read_text(encoding='utf-8'))
        by_variable = {entry['var_name']: entry for entry in entries}

        assert env_text.count(f'{FLAG}=true') == 1, process_root.name
        assert by_variable[FLAG] == EXPECTED_ENTRY, process_root.name
        assert set(by_variable) == _env_keys(env_path) - {'ENVIRONMENT'}, process_root.name
