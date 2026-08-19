from __future__ import annotations

from types import SimpleNamespace

from ada.processes.remanentes.composition import REMANENTES_JOB_DEFINITION, build_composition
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.kernel import Environment


def _configuration(tmp_path) -> ResolvedConfiguration:
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': str(tmp_path),
        'STORAGE_ACCOUNT_CONNECTION_STRING_REMANENTES': 'UseDevelopmentStorage=true',
        'STORAGE_ACCOUNT_CONTAINER_NAME_REMANENTES': 'dataproduct',
        'REMANENTES_SOURCE_TIMEZONE': 'America/Santiago',
        'REMANENTES_IDLE_SECONDS': '30',
    }
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
    )


def test_composition_passes_single_connection_and_identity(monkeypatch, tmp_path) -> None:
    import ada.processes.remanentes.composition as module

    captured = {}
    sentinel = SimpleNamespace(
        storage=object(), job=SimpleNamespace(run_iteration=lambda context: None)
    )

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(module, 'build_remanentes_data_producer', fake_builder)
    composition = build_composition(configuration=_configuration(tmp_path))

    assert composition.producer is sentinel
    assert captured['producer_key'] == 'remanentes'
    assert captured['dataset_namespace'] == ('remanentes',)
    assert captured['connection'].container_name == 'dataproduct'
    assert tuple(item.stream_key for item in captured['definitions']) == (
        'stocks',
        'extraibles',
        'no_extraibles',
    )


def test_job_is_run_once_for_platform_schedule() -> None:
    assert REMANENTES_JOB_DEFINITION.run_once is True
