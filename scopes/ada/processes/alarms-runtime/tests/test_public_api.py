import ada.processes.alarms_runtime as alarms_runtime
from ada.processes.alarms_runtime import (
    AlarmRuntimeComposition,
    AlarmRuntimeCompositionError,
    AlarmRuntimeGroup,
    __version__,
    build_alarm_runtime_composition,
    compose_engine_commit_record,
    decode_group_runtime_snapshot,
    encode_group_runtime_snapshot,
)


def test_public_api_and_version() -> None:
    assert AlarmRuntimeComposition.__name__ == 'AlarmRuntimeComposition'
    assert AlarmRuntimeCompositionError.__name__ == 'AlarmRuntimeCompositionError'
    assert AlarmRuntimeGroup.__name__ == 'AlarmRuntimeGroup'
    assert callable(build_alarm_runtime_composition)
    assert callable(compose_engine_commit_record)
    assert callable(decode_group_runtime_snapshot)
    assert callable(encode_group_runtime_snapshot)
    assert __version__ == '0.2.0'
    assert not hasattr(alarms_runtime, 'AlarmRuntimeDurability')
    assert not hasattr(alarms_runtime, 'AlarmRuntimePersistenceComposition')
    assert not hasattr(alarms_runtime, 'build_persistence_composition')
