from __future__ import annotations

from pathlib import Path

import pytest

from ada.processes.remanentes.bootstrap import load_configuration
from ada.processes.remanentes.errors import RemanentesProcessConfigurationError


def _environ(volume_path: str) -> dict[str, str]:
    return {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': volume_path,
        'STORAGE_ACCOUNT_CONNECTION_STRING_REMANENTES': 'UseDevelopmentStorage=true',
        'STORAGE_ACCOUNT_CONTAINER_NAME_REMANENTES': 'dataproduct',
        'REMANENTES_SOURCE_TIMEZONE': 'America/Santiago',
        'REMANENTES_IDLE_SECONDS': '30',
        'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'off',
    }


def test_local_configuration_requires_absolute_volume(tmp_path: Path) -> None:
    with pytest.raises(RemanentesProcessConfigurationError, match='absolute path'):
        load_configuration(process_root=tmp_path, environ=_environ('.local-volume'))


def test_local_configuration_accepts_absolute_volume(tmp_path: Path) -> None:
    volume = tmp_path / 'volume'
    configuration = load_configuration(
        process_root=tmp_path,
        environ=_environ(str(volume)),
    )
    assert configuration.require('VOLUMEN_PATH') == str(volume)
