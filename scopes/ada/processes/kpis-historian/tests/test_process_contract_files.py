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
    assert detail['template']['container']['name'] == 'kpis-historian-service'
    env = (_ROOT / '.env.detail').read_text(encoding='utf-8')
    assert 'APPLICATION=ada-operaciones-integradas-local' in env
    assert 'KPI_HISTORIAN_POLL_INTERVAL_SECONDS=1' in env
    assert 'KPI_APPLICATION=' not in env
