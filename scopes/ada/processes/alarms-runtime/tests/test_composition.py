from pathlib import Path

import pytest

from ada.processes.alarms_runtime import build_alarm_runtime_composition
from atlanticus.runtime import RuntimeConfiguration


def test_composition_uses_shared_volume_root(tmp_path: Path) -> None:
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada-alarms-runtime-local',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )

    composition = build_alarm_runtime_composition(runtime_configuration=configuration)

    assert composition.runtime_configuration is configuration
    assert composition.durability.persistence.paths.shared_volume_path == tmp_path
    assert composition.durability.persistence.paths.alarms_root == tmp_path / 'ada' / 'alarms'


def test_composition_rejects_wrong_configuration_type() -> None:
    with pytest.raises(TypeError, match='runtime_configuration'):
        build_alarm_runtime_composition(runtime_configuration=object())  # type: ignore[arg-type]
