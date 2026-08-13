from __future__ import annotations

from pathlib import Path

from azure.servicebus import ServiceBusClient, ServiceBusReceiveMode

import atlanticus.connectivity.service_bus as service_bus

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_CONNECTIVITY_ROOT = _PACKAGE_ROOT.parent


def test_public_api_and_version_are_stable() -> None:
    assert service_bus.__version__ == '0.1.0'
    assert service_bus.__all__ == [
        'ServiceBusAuthenticationError',
        'ServiceBusAuthorizationError',
        'ServiceBusConfigurationError',
        'ServiceBusConnectionError',
        'ServiceBusDelivery',
        'ServiceBusDeliveryState',
        'ServiceBusError',
        'ServiceBusMessage',
        'ServiceBusMessageError',
        'ServiceBusReceiveError',
        'ServiceBusSettings',
        'ServiceBusSettlementError',
        'ServiceBusTopicReceiver',
        '__version__',
    ]


def test_connector_has_no_mode_or_environment_configuration_surface() -> None:
    settings_source = (
        _PACKAGE_ROOT / 'src/atlanticus/connectivity/service_bus/settings.py'
    ).read_text()
    init_source = (
        _PACKAGE_ROOT / 'src/atlanticus/connectivity/service_bus/__init__.py'
    ).read_text()

    assert 'from_mapping' not in settings_source
    assert 'SERVICE_BUS_CONNECTION_STRING' not in settings_source
    assert 'SERVICE_BUS_TOPIC_NAME' not in settings_source
    assert 'SERVICE_BUS_SUBSCRIPTION_NAME' not in settings_source
    assert 'receive_mode' not in settings_source
    assert 'read_mode' not in settings_source
    assert 'ServiceBusReceiveMode' not in init_source
    assert 'ServiceBusReadMode' not in init_source


def test_receiver_contract_is_fixed_to_peek_lock_without_prefetch_or_sdk_retries() -> None:
    source = (_PACKAGE_ROOT / 'src/atlanticus/connectivity/service_bus/receiver.py').read_text()

    assert 'AzureServiceBusReceiveMode.PEEK_LOCK' in source
    assert 'prefetch_count=0' in source
    assert 'retry_total=0' in source
    assert 'peek_messages(' not in source
    assert 'RECEIVE_AND_DELETE' not in source


def test_pinned_sdk_exposes_required_sync_contract() -> None:
    pyproject = (_PACKAGE_ROOT / 'pyproject.toml').read_text()

    assert 'azure-servicebus==7.14.3' in pyproject
    assert callable(getattr(ServiceBusClient, 'from_connection_string', None))
    assert ServiceBusReceiveMode.PEEK_LOCK is not None


def test_service_bus_has_isolated_docker_integration_gate() -> None:
    compose = _CONNECTIVITY_ROOT / 'docker/service-bus/compose.yaml'
    check = (_CONNECTIVITY_ROOT / 'scripts/validation/check.sh').read_text()
    check_windows = (_CONNECTIVITY_ROOT / 'scripts/validation/check.bat').read_text()

    assert compose.is_file()
    compose_text = compose.read_text()
    assert 'servicebus-emulator:' in compose_text
    assert 'servicebus-mssql:' in compose_text
    assert 'service-bus-integration:' in compose_text
    assert 'platform: linux/amd64' not in compose_text
    assert 'atlanticus-service-bus-integration:local' in compose_text
    assert 'service-bus' in check
    assert 'service-bus' in check_windows
