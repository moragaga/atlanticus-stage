import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_process_contract_files_are_present_and_consistent() -> None:
    for name in (
        'FIRST_STEP.txt',
        '.env.detail',
        'config.detail.json',
        'secrets.detail.json',
        'pyproject.toml',
    ):
        assert (_ROOT / name).is_file()
    detail = json.loads((_ROOT / 'config.detail.json').read_text(encoding='utf-8'))
    assert detail['configuration']['replicaTimeout'] == 610
    assert detail['template']['container']['name'] == 'kpis-service'
    env = (_ROOT / '.env.detail').read_text(encoding='utf-8')
    assert 'PI_SOURCE=PI_WEB_API' in env
    assert 'PI_APPLICATION=ada-pi-web-api-local' in env
    assert 'KPI_POLL_INTERVAL_SECONDS=1' in env
