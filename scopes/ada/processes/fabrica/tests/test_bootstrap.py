from pathlib import Path

import pytest

from ada.processes.fabrica.bootstrap import load_configuration
from ada.processes.fabrica.errors import FabricaProcessConfigurationError


def _environ(volume_path: str) -> dict[str, str]:
    return {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': volume_path,
        'STORAGE_ACCOUNT_SAS_URL_FABRICA_PLANES': 'https://a.blob.core.windows.net/plans',
        'STORAGE_ACCOUNT_SAS_TOKEN_FABRICA_PLANES': 'sv=1',
        'STORAGE_ACCOUNT_SAS_URL_FABRICA_KPIS': 'https://b.blob.core.windows.net/kpis',
        'STORAGE_ACCOUNT_SAS_TOKEN_FABRICA_KPIS': 'sv=2',
        'FABRICA_IDLE_SECONDS': '5',
        'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'off',
    }


def test_local_configuration_requires_absolute_shared_volume(tmp_path: Path) -> None:
    with pytest.raises(FabricaProcessConfigurationError, match='absolute path'):
        load_configuration(process_root=tmp_path, environ=_environ('.local-volume'))


def test_local_configuration_accepts_absolute_shared_volume(tmp_path: Path) -> None:
    volume = tmp_path / 'volume'
    configuration = load_configuration(process_root=tmp_path, environ=_environ(str(volume)))
    assert configuration.require('VOLUMEN_PATH') == str(volume)
