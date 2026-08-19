class KpiEvaluationError(Exception):
    pass


class KpiInvalidValueError(KpiEvaluationError, ValueError):
    pass


class KpiDependencyNotRequestedError(KpiEvaluationError, KeyError):
    pass
