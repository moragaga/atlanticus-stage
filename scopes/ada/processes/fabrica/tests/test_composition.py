from types import SimpleNamespace

from ada.processes.fabrica.composition import build_composition
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.kernel import Environment


def _configuration(tmp_path):
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': str(tmp_path),
        'STORAGE_ACCOUNT_SAS_URL_FABRICA_PLANES': 'https://a.blob.core.windows.net/plans',
        'STORAGE_ACCOUNT_SAS_TOKEN_FABRICA_PLANES': 'sv=1',
        'STORAGE_ACCOUNT_SAS_URL_FABRICA_KPIS': 'https://b.blob.core.windows.net/kpis',
        'STORAGE_ACCOUNT_SAS_TOKEN_FABRICA_KPIS': 'sv=2',
        'FABRICA_IDLE_SECONDS': '5',
    }
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
    )


def test_composition_passes_named_storage_connections_and_process_identity(monkeypatch, tmp_path):
    import ada.processes.fabrica.composition as module

    captured = {}
    sentinel = SimpleNamespace(storages={}, job=SimpleNamespace(run_iteration=lambda context: None))

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(module, 'build_fabrica_data_producer', fake_builder)
    composition = build_composition(configuration=_configuration(tmp_path))

    assert composition.producer is sentinel
    assert captured['producer_key'] == 'fabrica'
    assert captured['dataset_namespace'] == ('fabrica',)
    assert set(captured['connections']) == {'planes', 'kpis'}
    assert captured['connections']['planes'].container_name == 'plans'
    assert captured['connections']['kpis'].container_name == 'kpis'
