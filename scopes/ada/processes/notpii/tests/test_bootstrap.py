from pathlib import Path

import pytest

from ada.processes.notpii.bootstrap import _require_absolute_volume_path
from ada.processes.notpii.errors import NotPiiProcessConfigurationError
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.kernel import Environment


def _configuration(volume_path: str) -> ResolvedConfiguration:
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': volume_path,
    }
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
    )


def test_volume_path_must_be_absolute(tmp_path: Path) -> None:
    with pytest.raises(NotPiiProcessConfigurationError, match='absolute'):
        _require_absolute_volume_path(_configuration('relative-volume'))
    assert _require_absolute_volume_path(_configuration(str(tmp_path))).require(
        'VOLUMEN_PATH'
    ) == str(tmp_path)
