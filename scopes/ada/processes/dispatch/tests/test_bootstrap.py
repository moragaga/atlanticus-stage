from pathlib import Path

import pytest

from ada.processes.dispatch.bootstrap import _require_absolute_volume_path


class _Configuration:
    def __init__(self, volume_path: str) -> None:
        self.volume_path = volume_path

    def require(self, key: str) -> str:
        assert key == 'VOLUMEN_PATH'
        return self.volume_path


def test_volume_path_must_be_absolute() -> None:
    with pytest.raises(Exception, match='VOLUMEN_PATH must be an absolute path'):
        _require_absolute_volume_path(_Configuration('relative/path'))


def test_absolute_volume_path_is_preserved(tmp_path: Path) -> None:
    configuration = _Configuration(str(tmp_path))
    assert _require_absolute_volume_path(configuration) is configuration
