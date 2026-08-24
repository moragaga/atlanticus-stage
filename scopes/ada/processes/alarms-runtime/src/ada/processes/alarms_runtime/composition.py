from __future__ import annotations

from dataclasses import dataclass

from ada.alarms.persistence import AlarmPersistence
from ada.processes.alarms_runtime.durability import AlarmRuntimeDurability
from atlanticus.runtime import RuntimeConfiguration


@dataclass(slots=True)
class AlarmRuntimePersistenceComposition:
    runtime_configuration: RuntimeConfiguration
    durability: AlarmRuntimeDurability


def build_persistence_composition(
    *,
    runtime_configuration: RuntimeConfiguration,
) -> AlarmRuntimePersistenceComposition:
    if not isinstance(runtime_configuration, RuntimeConfiguration):
        raise TypeError('runtime_configuration must be a RuntimeConfiguration')
    persistence = AlarmPersistence(shared_volume_path=runtime_configuration.volume_path)
    return AlarmRuntimePersistenceComposition(
        runtime_configuration=runtime_configuration,
        durability=AlarmRuntimeDurability(persistence=persistence),
    )
