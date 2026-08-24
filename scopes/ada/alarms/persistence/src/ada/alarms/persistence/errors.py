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
