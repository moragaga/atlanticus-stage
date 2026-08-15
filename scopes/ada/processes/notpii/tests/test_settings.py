import pytest

from ada.processes.notpii.composition import NOTPII_JOB_DEFINITION
from ada.processes.notpii.errors import NotPiiProcessConfigurationError
from ada.processes.notpii.settings import NotPiiSettings, configuration_specs
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.integrations.pi.contracts import PiExtractionMode
from atlanticus.kernel import Environment


def _configuration(**overrides: str) -> ResolvedConfiguration:
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': '/tmp/ada',
        'NOTPII_INTERPOLATED_SERVICE_BUS_CONNECTION_STRING': 'Endpoint=sb://one/;Key=value',
        'NOTPII_INTERPOLATED_SERVICE_BUS_TOPIC_NAME': 'interpolated',
        'NOTPII_INTERPOLATED_SERVICE_BUS_SUBSCRIPTION_NAME': 'materialization',
        'NOTPII_INTERPOLATED_SERVICE_BUS_MAX_WAIT_TIME_SECONDS': '10',
        'NOTPII_RECORDED_SERVICE_BUS_CONNECTION_STRING': 'Endpoint=sb://two/;Key=value',
        'NOTPII_RECORDED_SERVICE_BUS_TOPIC_NAME': 'recorded',
        'NOTPII_RECORDED_SERVICE_BUS_SUBSCRIPTION_NAME': 'materialization',
        'NOTPII_RECORDED_SERVICE_BUS_MAX_WAIT_TIME_SECONDS': '5',
        'NOTPII_RAW_BATCH_SIZE': '100000',
        'NOTPII_MAX_MESSAGE_COUNT': '10',
        **overrides,
    }
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
    )


def test_settings_use_only_active_mode_and_default_batch_contract() -> None:
    settings = NotPiiSettings.from_configuration(
        _configuration(),
        active_modes=(PiExtractionMode.INTERPOLATED,),
    )

    assert set(settings.service_buses) == {PiExtractionMode.INTERPOLATED}
    assert settings.max_message_count == 10
    assert settings.raw_batch_size == 100000


def test_settings_reject_invalid_batch_limit() -> None:
    with pytest.raises(NotPiiProcessConfigurationError, match='NOTPII_MAX_MESSAGE_COUNT'):
        NotPiiSettings.from_configuration(
            _configuration(NOTPII_MAX_MESSAGE_COUNT='0'),
            active_modes=(PiExtractionMode.INTERPOLATED,),
        )


def test_runtime_uses_robust_ten_minute_execution_and_lease_contract() -> None:
    assert NOTPII_JOB_DEFINITION.execution_timeout_seconds == 600
    assert NOTPII_JOB_DEFINITION.iteration_timeout_seconds == 240
    assert NOTPII_JOB_DEFINITION.shutdown_grace_seconds == 10
    assert NOTPII_JOB_DEFINITION.lease_timeout_seconds == 30
    assert NOTPII_JOB_DEFINITION.lease_renew_seconds == 10
    assert NOTPII_JOB_DEFINITION.lease_wait_seconds is None
    assert NOTPII_JOB_DEFINITION.sleep_seconds == 0


def test_configuration_specs_include_only_active_service_bus_mode() -> None:
    keys = {spec.key for spec in configuration_specs(active_modes=(PiExtractionMode.INTERPOLATED,))}
    assert 'NOTPII_INTERPOLATED_SERVICE_BUS_CONNECTION_STRING' in keys
    assert 'NOTPII_RECORDED_SERVICE_BUS_CONNECTION_STRING' not in keys
