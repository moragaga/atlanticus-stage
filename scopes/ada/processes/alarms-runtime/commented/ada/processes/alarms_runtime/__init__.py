# Expone la composición durable ya cerrada y, desde R3.1, el contrato puro de sesión de ejecución.
# La sesión comparte DataRequirement/DataLoadPlan con KPI sin introducir I/O ni semántica funcional nueva en Core.
from ada.processes.alarms_runtime.commit import compose_engine_commit_record
from ada.processes.alarms_runtime.composition import (
    AlarmRuntimeComposition,
    AlarmRuntimeGroup,
    build_alarm_runtime_composition,
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

__version__ = '0.3.0'

__all__ = [
    'AlarmEvaluatorContract',
    'AlarmEvaluatorRegistry',
    'AlarmExecutionEntry',
    'AlarmExecutionSession',
    'AlarmExecutionSessionError',
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
