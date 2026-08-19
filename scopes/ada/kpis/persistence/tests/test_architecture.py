from pathlib import Path

_ROOT = Path('src/ada/kpis/persistence')


def test_persistence_does_not_depend_on_processes_or_external_configuration() -> None:
    source = '\n'.join(path.read_text(encoding='utf-8') for path in _ROOT.glob('*.py'))

    assert 'ada.processes' not in source
    assert 'atlanticus.configuration' not in source
    assert 'os.environ' not in source
    assert 'cosmos' not in source.lower()
    assert 'PI_WEB_API' not in source
    assert 'NOTPII' not in source
