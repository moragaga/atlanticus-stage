# API pública de alarms-runtime. Los comentarios explican agrupaciones sin alterar contratos.
from ada.processes.alarms_runtime.adoption import (
    AlarmConfigurationRevision,
    AlarmConfigurationRevisionError,
    ConfigurationAdoptionChange,
    ConfigurationAdoptionDisposition,
    ConfigurationAdoptionPlan,
    ConfigurationAdoptionPlanError,
    ConfigurationAdoptionRejectionReason,
    plan_configuration_adoption,
)
from ada.processes.alarms_runtime.adoption_execution import (
    AlarmConfigurationAdoptionExecutor,
    ConfigurationAdoptionExecutionError,
    ConfigurationAdoptionExecutionResult,
    ConfigurationAdoptionGroupResult,
)
from ada.processes.alarms_runtime.commit import compose_engine_commit_record
from ada.processes.alarms_runtime.composition import (
    AlarmRuntimeComposition,
    AlarmRuntimeGroup,
    build_alarm_runtime_composition,
)
from ada.processes.alarms_runtime.consumer import (
    AlarmDurableInputConsumer,
    AlarmDurableInputConsumerError,
)
from ada.processes.alarms_runtime.cycle import (
    AlarmCommitTimeProvider,
    AlarmGroupCycleResult,
    AlarmOperationalCycle,
    AlarmOperationalCycleError,
    AlarmOperationalCycleResult,
)
from ada.processes.alarms_runtime.inputs import (
    AlarmInputCursor,
    AlarmInputLocator,
    AlarmInputRecord,
    AlarmInputSource,
    AlarmInputStream,
    AlarmOperationalInputs,
    AlarmPendingDeactivationRequest,
)
from ada.processes.alarms_runtime.iteration import (
    AlarmExecutionIteration,
    AlarmExecutionIterationError,
    AlarmIterationLoader,
    AlarmIterationSourceLoader,
)

# Contratos puros R3.4A para resolver una pareja publicada sin I/O físico.
from ada.processes.alarms_runtime.revision_file import (
    FileRuntimeRevisionCache,
    FileRuntimeRevisionSource,
)
from ada.processes.alarms_runtime.revision_resolution import (
    RUNTIME_MANIFEST_SCHEMA_VERSION,
    RuntimeManifest,
    RuntimeRevisionBundle,
    RuntimeRevisionCache,
    RuntimeRevisionCacheError,
    RuntimeRevisionContractError,
    RuntimeRevisionDecoder,
    RuntimeRevisionDocument,
    RuntimeRevisionOrigin,
    RuntimeRevisionResolution,
    RuntimeRevisionSource,
    RuntimeRevisionSourceError,
)
from ada.processes.alarms_runtime.revision_resolver import (
    RuntimeRevisionResolver,
    RuntimeRevisionResolverError,
)
from ada.processes.alarms_runtime.session import (
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    AlarmExecutionEntry,
    AlarmExecutionSession,
    AlarmExecutionSessionError,
    build_alarm_execution_session,
)
from ada.processes.alarms_runtime.snapshot import (
    AlarmRuntimeCompositionError,
    decode_group_runtime_snapshot,
    encode_group_runtime_snapshot,
)

# R3.4A agrega contratos públicos de resolución; por eso incrementa la versión minor.
__version__ = '0.11.0'

__all__ = [
    'AlarmConfigurationRevision',
    'AlarmConfigurationRevisionError',
    'AlarmConfigurationAdoptionExecutor',
    'ConfigurationAdoptionChange',
    'ConfigurationAdoptionDisposition',
    'ConfigurationAdoptionExecutionError',
    'ConfigurationAdoptionExecutionResult',
    'ConfigurationAdoptionGroupResult',
    'ConfigurationAdoptionPlan',
    'ConfigurationAdoptionPlanError',
    'ConfigurationAdoptionRejectionReason',
    'plan_configuration_adoption',
    'AlarmCommitTimeProvider',
    'AlarmDurableInputConsumer',
    'AlarmDurableInputConsumerError',
    'AlarmEvaluatorContract',
    'AlarmEvaluatorRegistry',
    'AlarmExecutionEntry',
    'AlarmExecutionIteration',
    'AlarmExecutionIterationError',
    'AlarmExecutionSession',
    'AlarmExecutionSessionError',
    'AlarmGroupCycleResult',
    'AlarmInputCursor',
    'AlarmInputLocator',
    'AlarmInputRecord',
    'AlarmInputSource',
    'AlarmInputStream',
    'AlarmIterationLoader',
    'AlarmIterationSourceLoader',
    'AlarmOperationalCycle',
    'AlarmOperationalInputs',
    'AlarmPendingDeactivationRequest',
    'AlarmOperationalCycleError',
    'AlarmOperationalCycleResult',
    'AlarmRuntimeComposition',
    'AlarmRuntimeCompositionError',
    'AlarmRuntimeGroup',
    'FileRuntimeRevisionCache',
    'FileRuntimeRevisionSource',
    'RUNTIME_MANIFEST_SCHEMA_VERSION',
    'RuntimeManifest',
    'RuntimeRevisionBundle',
    'RuntimeRevisionCache',
    'RuntimeRevisionCacheError',
    'RuntimeRevisionContractError',
    'RuntimeRevisionDecoder',
    'RuntimeRevisionDocument',
    'RuntimeRevisionOrigin',
    'RuntimeRevisionResolution',
    'RuntimeRevisionSource',
    'RuntimeRevisionSourceError',
    'RuntimeRevisionResolver',
    'RuntimeRevisionResolverError',
    '__version__',
    'build_alarm_execution_session',
    'build_alarm_runtime_composition',
    'compose_engine_commit_record',
    'decode_group_runtime_snapshot',
    'encode_group_runtime_snapshot',
]
