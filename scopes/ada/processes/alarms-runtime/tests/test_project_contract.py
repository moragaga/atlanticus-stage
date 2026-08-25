import tomllib
from pathlib import Path

_ROOT = Path(__file__).parents[1]


def test_project_contract_pins_productive_dependencies() -> None:
    project = tomllib.loads((_ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']

    assert project['version'] == '0.10.0'
    assert project['requires-python'] == '==3.14.2'
    assert 'ada-alarms-core==0.7.0' in project['dependencies']
    assert 'ada-alarms-persistence==0.2.0' in project['dependencies']
    assert 'ada-operational-data-core==0.1.0' in project['dependencies']
    assert 'ada-operational-data-planner==0.1.0' in project['dependencies']
    assert 'ada-operational-data-sources==0.1.0' in project['dependencies']
    assert 'atlanticus-job-runtime==0.7.0' in project['dependencies']
    assert 'atlanticus-state==0.2.0' in project['dependencies']
    assert not any(item.startswith('atlanticus-datasets-') for item in project['dependencies'])


def test_runtime_composition_remains_library_without_process_launcher() -> None:
    source = tomllib.loads((_ROOT / 'pyproject.toml').read_text(encoding='utf-8'))

    assert 'scripts' not in source['project']
    assert 'atlanticus' not in source.get('tool', {})


def test_lock_preserves_alarm_runtime_and_shared_data_baselines() -> None:
    lock = tomllib.loads((_ROOT / 'uv.lock').read_text(encoding='utf-8'))
    versions = {item['name']: item.get('version') for item in lock['package']}

    assert versions['ada-alarms-runtime-process'] == '0.10.0'
    assert versions['ada-alarms-core'] == '0.7.0'
    assert versions['ada-alarms-persistence'] == '0.2.0'
    assert versions['ada-operational-data-core'] == '0.1.0'
    assert versions['ada-operational-data-planner'] == '0.1.0'
    assert versions['ada-operational-data-sources'] == '0.1.0'
    assert versions['atlanticus-job-runtime'] == '0.7.0'
    assert versions['atlanticus-state'] == '0.2.0'
