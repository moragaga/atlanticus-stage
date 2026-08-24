# Espejo pedagógico de la jerarquía de errores de Alarm Persistence.
# Los errores distinguen validación, lectura, escritura, corrupción y pérdida de autoridad.
# La persistencia debe fallar de forma cerrada ante corrupción durable o inconsistencias físicas.
# No se agrega comportamiento distinto al código productivo; sólo contexto para mantenimiento.

from __future__ import annotations


class AlarmPersistenceError(RuntimeError):
    pass


class AlarmPersistenceValidationError(AlarmPersistenceError, ValueError):
    pass


class AlarmPersistenceCorruptionError(AlarmPersistenceError):
    pass


class AlarmPersistenceConflictError(AlarmPersistenceError):
    pass


class AlarmPersistenceWriteError(AlarmPersistenceError):
    pass


class AlarmRecoveryRequiredError(AlarmPersistenceError):
    pass
