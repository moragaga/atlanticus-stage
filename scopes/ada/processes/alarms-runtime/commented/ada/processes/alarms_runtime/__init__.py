# La API pública ahora expone cuatro niveles: sesión congelada, inputs frescos, ciclo
# data-driven y composición durable. R3.3A aún no convierte la library en launcher.
from ada.processes.alarms_runtime.commit import compose_engine_commit_record
from ada.processes.alarms_runtime.composition import (
    AlarmRuntimeComposition,
    AlarmRuntimeGroup,
    build_alarm_runtime_composition,
)
from ada.processes.alarms_runtime.cycle import (
    AlarmCommitTimeProvider,
    AlarmGroupCycleResult,
    AlarmOperationalCycle,
    AlarmOperationalCycleError,
    AlarmOperationalCycleResult,
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

__version__ = '0.5.1'

__all__ = [
    'AlarmCommitTimeProvider',
    'AlarmEvaluatorContract',
    'AlarmEvaluatorRegistry',
    'AlarmExecutionEntry',
    'AlarmExecutionIteration',
    'AlarmExecutionIterationError',
    'AlarmExecutionSession',
    'AlarmExecutionSessionError',
    'AlarmGroupCycleResult',
    'AlarmIterationLoader',
    'AlarmIterationSourceLoader',
    'AlarmOperationalCycle',
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
