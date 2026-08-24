# Espejo pedagógico de la API pública de Alarm Persistence.
# Este archivo conserva exactamente los mismos tokens ejecutables que producción.
# Las notas se mantienen fuera del código para que Ruff pueda formatear ambos árboles de forma idéntica.
# La API publicada expone contratos de persistencia, modelos, errores y la versión del paquete.

from ada.alarms.persistence.errors import (
    AlarmPersistenceConflictError,
    AlarmPersistenceCorruptionError,
    AlarmPersistenceError,
    AlarmPersistenceValidationError,
    AlarmPersistenceWriteError,
    AlarmRecoveryRequiredError,
)
from ada.alarms.persistence.models import (
    ENGINE_COMMIT_RECORD_SCHEMA_VERSION,
    GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION,
    JOURNAL_HEAD_SCHEMA_VERSION,
    CommitBatchResult,
    EngineCommitMetadata,
    EngineCommitRecord,
    GroupRuntimeSnapshot,
    JournalEntry,
    JournalHead,
    JournalPosition,
    RecoveryResult,
    parse_segment_id,
    segment_id_for_evaluated_at,
)
from ada.alarms.persistence.paths import AlarmPersistencePaths
from ada.alarms.persistence.store import AlarmPersistence, AuthorityCheck, MutationFence

__version__ = '0.1.0'

__all__ = [
    'ENGINE_COMMIT_RECORD_SCHEMA_VERSION',
    'GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION',
    'JOURNAL_HEAD_SCHEMA_VERSION',
    'AlarmPersistence',
    'AlarmPersistenceConflictError',
    'AlarmPersistenceCorruptionError',
    'AlarmPersistenceError',
    'AlarmPersistencePaths',
    'AlarmPersistenceValidationError',
    'AlarmPersistenceWriteError',
    'AlarmRecoveryRequiredError',
    'AuthorityCheck',
    'CommitBatchResult',
    'EngineCommitMetadata',
    'EngineCommitRecord',
    'GroupRuntimeSnapshot',
    'JournalEntry',
    'JournalHead',
    'JournalPosition',
    'MutationFence',
    'RecoveryResult',
    '__version__',
    'parse_segment_id',
    'segment_id_for_evaluated_at',
]
