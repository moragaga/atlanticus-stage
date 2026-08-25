import ada.processes.alarms_runtime as alarms_runtime
from ada.processes.alarms_runtime import (
    AlarmCommitTimeProvider,
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    AlarmExecutionEntry,
    AlarmExecutionIteration,
    AlarmExecutionIterationError,
    AlarmExecutionSession,
    AlarmExecutionSessionError,
    AlarmGroupCycleResult,
    AlarmIterationLoader,
    AlarmIterationSourceLoader,
    AlarmOperationalCycle,
    AlarmOperationalCycleError,
    AlarmOperationalCycleResult,
    AlarmRuntimeComposition,
    AlarmRuntimeCompositionError,
    AlarmRuntimeGroup,
    __version__,
    build_alarm_execution_session,
    build_alarm_runtime_composition,
    compose_engine_commit_record,
    decode_group_runtime_snapshot,
    encode_group_runtime_snapshot,
)


def test_public_api_and_version() -> None:
    assert AlarmCommitTimeProvider.__name__ == 'AlarmCommitTimeProvider'
    assert AlarmEvaluatorContract.__name__ == 'AlarmEvaluatorContract'
    assert AlarmEvaluatorRegistry.__name__ == 'AlarmEvaluatorRegistry'
    assert AlarmExecutionEntry.__name__ == 'AlarmExecutionEntry'
    assert AlarmExecutionIteration.__name__ == 'AlarmExecutionIteration'
    assert AlarmExecutionIterationError.__name__ == 'AlarmExecutionIterationError'
    assert AlarmExecutionSession.__name__ == 'AlarmExecutionSession'
    assert AlarmExecutionSessionError.__name__ == 'AlarmExecutionSessionError'
    assert AlarmIterationLoader.__name__ == 'AlarmIterationLoader'
    assert AlarmIterationSourceLoader.__name__ == 'AlarmIterationSourceLoader'
    assert AlarmGroupCycleResult.__name__ == 'AlarmGroupCycleResult'
    assert AlarmOperationalCycle.__name__ == 'AlarmOperationalCycle'
    assert AlarmOperationalCycleError.__name__ == 'AlarmOperationalCycleError'
    assert AlarmOperationalCycleResult.__name__ == 'AlarmOperationalCycleResult'
    assert AlarmRuntimeComposition.__name__ == 'AlarmRuntimeComposition'
    assert AlarmRuntimeCompositionError.__name__ == 'AlarmRuntimeCompositionError'
    assert AlarmRuntimeGroup.__name__ == 'AlarmRuntimeGroup'
    assert callable(build_alarm_execution_session)
    assert callable(build_alarm_runtime_composition)
    assert callable(compose_engine_commit_record)
    assert callable(decode_group_runtime_snapshot)
    assert callable(encode_group_runtime_snapshot)
    assert __version__ == '0.5.0'
    assert not hasattr(alarms_runtime, 'AlarmRuntimeDurability')
    assert not hasattr(alarms_runtime, 'AlarmRuntimePersistenceComposition')
    assert not hasattr(alarms_runtime, 'build_persistence_composition')
