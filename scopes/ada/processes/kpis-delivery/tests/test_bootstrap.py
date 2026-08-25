from pathlib import Path

import pytest

from ada.processes.kpis_delivery import KpiDeliveryConfigurationError, load_configuration


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada-operaciones-integradas-local',
        'VOLUMEN_PATH': str(tmp_path / 'runtime'),
        'COSMOS_CONSUMPTION_ENDPOINT': 'http://localhost:8081',
        'COSMOS_CONSUMPTION_KEY': 'secret',
        'COSMOS_CONSUMPTION_DATABASE_NAME': 'ada',
        'KPI_DELIVERY_POLL_INTERVAL_SECONDS': '1',
        'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'off',
    }


def test_local_bootstrap_loads_declared_process_configuration(tmp_path) -> None:
    configuration = load_configuration(process_root=tmp_path, environ=_environment(tmp_path))

    assert configuration.require('APPLICATION') == 'ada-operaciones-integradas-local'
    assert configuration.require('COSMOS_CONSUMPTION_DATABASE_NAME') == 'ada'
    assert 'COSMOS_CONSUMPTION_KEY' in configuration.sensitive_keys
    assert 'COSMOS_CONSUMPTION_CONTAINER_NAME' not in configuration.values


def test_bootstrap_requires_absolute_volume_path(tmp_path) -> None:
    environment = _environment(tmp_path)
    environment['VOLUMEN_PATH'] = 'relative/runtime'

    with pytest.raises(
        KpiDeliveryConfigurationError,
        match='VOLUMEN_PATH must be an absolute path',
    ):
        load_configuration(process_root=tmp_path, environ=environment)


def test_bootstrap_exposes_executable_main_without_adapter_injection() -> None:
    import inspect

    from ada.processes.kpis_delivery.bootstrap import main, run

    assert callable(main)
    assert 'configuration' not in inspect.signature(run).parameters
