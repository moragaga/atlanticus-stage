import pytest

from ada.processes.kpis_historian.errors import KpiHistorianConfigurationError
from ada.processes.kpis_historian.settings import KpiHistorianSettings, configuration_specs
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.kernel import Environment


def _configuration(**values):
    base = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada-operaciones-integradas-local',
        'VOLUMEN_PATH': '/tmp/atlanticus',
        'KPI_HISTORIAN_POLL_INTERVAL_SECONDS': '1',
    }
    base.update(values)
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=base,
        sources={key: ConfigurationSource.PROCESS for key in base},
    )


def test_historian_uses_only_shared_application_scope_and_operational_poll_setting() -> None:
    settings = KpiHistorianSettings.from_configuration(
        _configuration(KPI_HISTORIAN_POLL_INTERVAL_SECONDS='7')
    )
    keys = {spec.key for spec in configuration_specs()}

    assert settings.poll_interval_seconds == 7
    assert 'APPLICATION' in keys
    assert 'VOLUMEN_PATH' in keys
    assert 'KPI_HISTORIAN_POLL_INTERVAL_SECONDS' in keys
    assert 'KPI_APPLICATION' not in keys
    assert 'HISTORIAN_APPLICATION' not in keys


def test_poll_interval_must_be_positive() -> None:
    with pytest.raises(KpiHistorianConfigurationError, match='greater than zero'):
        KpiHistorianSettings.from_configuration(
            _configuration(KPI_HISTORIAN_POLL_INTERVAL_SECONDS='0')
        )
