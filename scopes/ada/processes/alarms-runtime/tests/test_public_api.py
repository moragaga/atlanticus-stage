from ada.processes.alarms_runtime import (
    AlarmRuntimeDurability,
    AlarmRuntimePersistenceComposition,
    __version__,
    build_persistence_composition,
)


def test_public_api_and_version() -> None:
    assert AlarmRuntimeDurability.__name__ == 'AlarmRuntimeDurability'
    assert AlarmRuntimePersistenceComposition.__name__ == 'AlarmRuntimePersistenceComposition'
    assert callable(build_persistence_composition)
    assert __version__ == '0.1.0'
