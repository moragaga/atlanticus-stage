from types import SimpleNamespace

from ada.processes.dispatch.composition import build_composition
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.kernel import Environment


def _configuration(tmp_path):
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': str(tmp_path),
        'SQL_CONNECTION_STRING_DISPATCH': 'Server=localhost;Database=test',
    }
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
    )


def test_composition_passes_process_identity_to_sql_producer(monkeypatch, tmp_path):
    import ada.processes.dispatch.composition as module

    captured = {}
    sentinel = SimpleNamespace(job=SimpleNamespace(run_iteration=lambda context: None))

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(module, 'build_sql_data_producer', fake_builder)
    composition = build_composition(configuration=_configuration(tmp_path))

    assert composition.producer is sentinel
    assert captured['producer_key'] == 'dispatch'
    assert captured['dataset_namespace'] == ('dispatch',)
    assert captured['missing_scope_fact_name'] == 'missing_shift_ids'
    assert type(captured['scope_provider']).__name__ == 'DispatchShiftScopeProvider'
    assert captured['retry_policy'].attempts == 10
    assert captured['retry_policy'].delay_seconds == 5.0
