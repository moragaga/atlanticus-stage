# R3.3C.0 expone contratos de revisión y adopción sin ejecutar todavía la transición.
from ada.processes.alarms_runtime.adoption import (
    AlarmConfigurationRevision,
    AlarmConfigurationRevisionError,
    ConfigurationAdoptionChange,
    ConfigurationAdoptionDisposition,
    ConfigurationAdoptionPlan,
    ConfigurationAdoptionPlanError,
    ConfigurationAdoptionRejectionReason,
)

# Este módulo define la API pública estable del proceso Alarm Runtime.
# R3.3B mantiene el consumer durable sin exponer detalles físicos de Storage.
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
)
from ada.processes.alarms_runtime.iteration import (
    AlarmExecutionIteration,
    AlarmExecutionIterationError,
    AlarmIterationLoader,
    AlarmIterationSourceLoader,
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

__version__ = '0.7.0'

__all__ = [
    'AlarmConfigurationRevision',
    'AlarmConfigurationRevisionError',
    'ConfigurationAdoptionChange',
    'ConfigurationAdoptionDisposition',
    'ConfigurationAdoptionPlan',
    'ConfigurationAdoptionPlanError',
    'ConfigurationAdoptionRejectionReason',
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
    'AlarmOperationalCycleError',
    'AlarmOperationalCycleResult',
    'AlarmRuntimeComposition',
    'AlarmRuntimeCompositionError',
    'AlarmRuntimeGroup',
    '__version__',
    'build_alarm_execution_session',
    'build_alarm_runtime_composition',
    'compose_engine_commit_record',
    'decode_group_runtime_snapshot',
    'encode_group_runtime_snapshot',
]
