class KpiPersistenceError(Exception):
    pass


class KpiPersistenceValidationError(KpiPersistenceError, ValueError):
    pass


class KpiPersistenceCorruptionError(KpiPersistenceError, ValueError):
    pass


class KpiEvaluationConflictError(KpiPersistenceError):
    pass


class KpiWatermarkRegressionError(KpiPersistenceError, ValueError):
    pass
