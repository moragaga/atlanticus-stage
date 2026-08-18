from ada.processes.blockgrade.composition import BLOCKGRADE_JOB_DEFINITION
from ada.processes.blockgrade.settings import BlockgradeSettings, configuration_specs
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.kernel import Environment


def _configuration(**overrides: str) -> ResolvedConfiguration:
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': '/tmp/ada',
        'SQL_CONNECTION_STRING_BLOCKGRADE': 'Server=localhost;Database=test',
        **overrides,
    }
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
    )


def test_runtime_matches_current_process_standard() -> None:
    definition = BLOCKGRADE_JOB_DEFINITION

    assert definition.iteration_timeout_seconds == 240
    assert definition.execution_timeout_seconds == 600
    assert definition.shutdown_grace_seconds == 10
    assert definition.lease_timeout_seconds == 30
    assert definition.lease_renew_seconds == 10
    assert definition.lease_wait_seconds is None
    assert definition.lease_poll_seconds == 1


def test_process_maps_named_sql_retry_configuration() -> None:
    defaults = BlockgradeSettings.from_configuration(_configuration())
    configured = BlockgradeSettings.from_configuration(
        _configuration(
            BLOCKGRADE_SQL_RETRY_ATTEMPTS='6',
            BLOCKGRADE_SQL_RETRY_DELAY_SECONDS='2.5',
        )
    )

    assert defaults.retry_policy.attempts == 10
    assert defaults.retry_policy.delay_seconds == 5.0
    assert configured.retry_policy.attempts == 6
    assert configured.retry_policy.delay_seconds == 2.5


def test_configuration_specs_include_named_sql_connection() -> None:
    keys = {spec.key for spec in configuration_specs()}

    assert 'SQL_CONNECTION_STRING_BLOCKGRADE' in keys
    assert 'BLOCKGRADE_SQL_RETRY_ATTEMPTS' in keys
    assert 'BLOCKGRADE_SQL_RETRY_DELAY_SECONDS' in keys
