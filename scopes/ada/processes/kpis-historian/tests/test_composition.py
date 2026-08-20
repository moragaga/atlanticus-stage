from ada.processes.kpis_historian.composition import build_composition
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.kernel import Environment


def _configuration(tmp_path):
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada-operaciones-integradas-local',
        'VOLUMEN_PATH': str(tmp_path),
        'KPI_HISTORIAN_POLL_INTERVAL_SECONDS': '3',
        'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'off',
    }
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
    )


def test_composition_uses_shared_application_and_stable_service_identity(tmp_path) -> None:
    composition = build_composition(configuration=_configuration(tmp_path))

    assert composition.runtime_configuration.application == 'ada-operaciones-integradas-local'
    assert composition.runtime_configuration.application_root == (
        tmp_path / 'ada-operaciones-integradas-local'
    )
    assert composition.job_definition.service_name == 'kpis-historian'
    assert composition.job_definition.job_key == 'kpis-historian'
    assert composition.job_definition.sleep_seconds == 3
