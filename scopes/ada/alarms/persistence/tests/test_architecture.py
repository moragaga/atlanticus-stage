from pathlib import Path

_ROOT = Path('src/ada/alarms/persistence')


def test_persistence_keeps_runtime_and_external_infrastructure_out_of_domain_storage() -> None:
    source = '\n'.join(path.read_text(encoding='utf-8') for path in _ROOT.glob('*.py'))

    assert 'ada.processes' not in source
    assert 'atlanticus.runtime' not in source
    assert 'atlanticus.configuration' not in source
    assert 'os.environ' not in source
    assert 'cosmos' not in source.lower()
    assert 'redis' not in source.lower()
    assert 'azure' not in source.lower()


def test_persistence_reuses_atlanticus_json_and_state_primitives() -> None:
    source = '\n'.join(path.read_text(encoding='utf-8') for path in _ROOT.glob('*.py'))

    assert 'atlanticus.json' in source
    assert 'AtomicJsonStore' in source
