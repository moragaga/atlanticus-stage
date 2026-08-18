from ada.processes.dispatch.composition import DISPATCH_JOB_DEFINITION
from ada.processes.dispatch.settings import DispatchSqlRetryPolicy, configuration_specs


def test_runtime_matches_current_process_standard() -> None:
    definition = DISPATCH_JOB_DEFINITION

    assert definition.iteration_timeout_seconds == 240
    assert definition.execution_timeout_seconds == 600
    assert definition.shutdown_grace_seconds == 10
    assert definition.lease_timeout_seconds == 30
    assert definition.lease_renew_seconds == 10
    assert definition.lease_wait_seconds is None
    assert definition.lease_poll_seconds == 1


def test_retry_policy_defaults_are_stable() -> None:
    policy = DispatchSqlRetryPolicy.from_mapping({})

    assert policy.attempts == 10
    assert policy.delay_seconds == 5.0


def test_configuration_specs_include_named_sql_connection() -> None:
    keys = {spec.key for spec in configuration_specs()}

    assert 'SQL_CONNECTION_STRING_DISPATCH' in keys
    assert 'DISPATCH_SQL_RETRY_ATTEMPTS' in keys
    assert 'DISPATCH_SQL_RETRY_DELAY_SECONDS' in keys
