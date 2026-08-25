import ada.processes.alarms_runtime as alarms_runtime
from ada.processes.alarms_runtime import (
    RUNTIME_MANIFEST_SCHEMA_VERSION,
    AlarmCommitTimeProvider,
    AlarmConfigurationAdoptionExecutor,
    AlarmConfigurationRevision,
    AlarmConfigurationRevisionError,
    AlarmDurableInputConsumer,
    AlarmDurableInputConsumerError,
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    AlarmExecutionEntry,
    AlarmExecutionIteration,
    AlarmExecutionIterationError,
    AlarmExecutionSession,
    AlarmExecutionSessionError,
    AlarmGroupCycleResult,
    AlarmInputCursor,
    AlarmInputLocator,
    AlarmInputRecord,
    AlarmInputSource,
    AlarmInputStream,
    AlarmIterationLoader,
    AlarmIterationSourceLoader,
    AlarmOperationalCycle,
    AlarmOperationalCycleError,
    AlarmOperationalCycleResult,
    AlarmOperationalInputs,
    AlarmPendingDeactivationRequest,
    AlarmRuntimeComposition,
    AlarmRuntimeCompositionError,
    AlarmRuntimeGroup,
    ConfigurationAdoptionChange,
    ConfigurationAdoptionDisposition,
    ConfigurationAdoptionExecutionError,
    ConfigurationAdoptionExecutionResult,
    ConfigurationAdoptionGroupResult,
    ConfigurationAdoptionPlan,
    ConfigurationAdoptionPlanError,
    ConfigurationAdoptionRejectionReason,
    FileRuntimeRevisionCache,
    FileRuntimeRevisionSource,
    ResolvedRuntimeRevision,
    RuntimeManifest,
    RuntimeRevisionBundle,
    RuntimeRevisionCache,
    RuntimeRevisionCacheError,
    RuntimeRevisionContractError,
    RuntimeRevisionDecoder,
    RuntimeRevisionDocument,
    RuntimeRevisionOrigin,
    RuntimeRevisionResolution,
    RuntimeRevisionResolver,
    RuntimeRevisionResolverError,
    RuntimeRevisionSource,
    RuntimeRevisionSourceError,
    __version__,
    build_alarm_execution_session,
    build_alarm_runtime_composition,
    compose_engine_commit_record,
    decode_group_runtime_snapshot,
    encode_group_runtime_snapshot,
    plan_configuration_adoption,
)


def test_public_api_and_version() -> None:
    assert AlarmConfigurationRevision.__name__ == 'AlarmConfigurationRevision'
    assert AlarmConfigurationAdoptionExecutor.__name__ == 'AlarmConfigurationAdoptionExecutor'
    assert AlarmConfigurationRevisionError.__name__ == 'AlarmConfigurationRevisionError'
    assert AlarmCommitTimeProvider.__name__ == 'AlarmCommitTimeProvider'
    assert AlarmDurableInputConsumer.__name__ == 'AlarmDurableInputConsumer'
    assert AlarmDurableInputConsumerError.__name__ == 'AlarmDurableInputConsumerError'
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
    assert AlarmInputCursor.__name__ == 'AlarmInputCursor'
    assert AlarmInputLocator.__name__ == 'AlarmInputLocator'
    assert AlarmInputRecord.__name__ == 'AlarmInputRecord'
    assert AlarmInputSource.__name__ == 'AlarmInputSource'
    assert AlarmInputStream.__name__ == 'AlarmInputStream'
    assert AlarmOperationalCycle.__name__ == 'AlarmOperationalCycle'
    assert AlarmOperationalInputs.__name__ == 'AlarmOperationalInputs'
    assert AlarmPendingDeactivationRequest.__name__ == 'AlarmPendingDeactivationRequest'
    assert AlarmOperationalCycleError.__name__ == 'AlarmOperationalCycleError'
    assert AlarmOperationalCycleResult.__name__ == 'AlarmOperationalCycleResult'
    assert AlarmRuntimeComposition.__name__ == 'AlarmRuntimeComposition'
    assert AlarmRuntimeCompositionError.__name__ == 'AlarmRuntimeCompositionError'
    assert AlarmRuntimeGroup.__name__ == 'AlarmRuntimeGroup'
    assert ConfigurationAdoptionChange.__name__ == 'ConfigurationAdoptionChange'
    assert ConfigurationAdoptionDisposition.__name__ == 'ConfigurationAdoptionDisposition'
    assert ConfigurationAdoptionExecutionError.__name__ == 'ConfigurationAdoptionExecutionError'
    assert ConfigurationAdoptionExecutionResult.__name__ == 'ConfigurationAdoptionExecutionResult'
    assert ConfigurationAdoptionGroupResult.__name__ == 'ConfigurationAdoptionGroupResult'
    assert ConfigurationAdoptionPlan.__name__ == 'ConfigurationAdoptionPlan'
    assert ConfigurationAdoptionPlanError.__name__ == 'ConfigurationAdoptionPlanError'
    assert ConfigurationAdoptionRejectionReason.__name__ == 'ConfigurationAdoptionRejectionReason'
    assert FileRuntimeRevisionCache.__name__ == 'FileRuntimeRevisionCache'
    assert FileRuntimeRevisionSource.__name__ == 'FileRuntimeRevisionSource'
    assert RUNTIME_MANIFEST_SCHEMA_VERSION == 'alarm-runtime-manifest.v1'
    assert ResolvedRuntimeRevision.__name__ == 'ResolvedRuntimeRevision'
    assert RuntimeManifest.__name__ == 'RuntimeManifest'
    assert RuntimeRevisionBundle.__name__ == 'RuntimeRevisionBundle'
    assert RuntimeRevisionCache.__name__ == 'RuntimeRevisionCache'
    assert RuntimeRevisionCacheError.__name__ == 'RuntimeRevisionCacheError'
    assert RuntimeRevisionContractError.__name__ == 'RuntimeRevisionContractError'
    assert RuntimeRevisionDecoder.__name__ == 'RuntimeRevisionDecoder'
    assert RuntimeRevisionDocument is not None
    assert RuntimeRevisionOrigin.__name__ == 'RuntimeRevisionOrigin'
    assert RuntimeRevisionResolution.__name__ == 'RuntimeRevisionResolution'
    assert RuntimeRevisionResolver.__name__ == 'RuntimeRevisionResolver'
    assert RuntimeRevisionResolverError.__name__ == 'RuntimeRevisionResolverError'
    assert RuntimeRevisionSource.__name__ == 'RuntimeRevisionSource'
    assert RuntimeRevisionSourceError.__name__ == 'RuntimeRevisionSourceError'
    assert callable(build_alarm_execution_session)
    assert callable(build_alarm_runtime_composition)
    assert callable(compose_engine_commit_record)
    assert callable(decode_group_runtime_snapshot)
    assert callable(encode_group_runtime_snapshot)
    assert callable(plan_configuration_adoption)
    assert __version__ == '0.12.0'
    assert not hasattr(alarms_runtime, 'AlarmRuntimeDurability')
    assert not hasattr(alarms_runtime, 'AlarmRuntimePersistenceComposition')
    assert not hasattr(alarms_runtime, 'build_persistence_composition')
