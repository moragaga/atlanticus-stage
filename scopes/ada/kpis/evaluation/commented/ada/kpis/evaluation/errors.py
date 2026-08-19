# Errores propios de la evaluación KPI.
# Se separa valor inválido de error de ejecución para producir estados distintos.
class KpiEvaluationError(Exception):
    pass


class KpiInvalidValueError(KpiEvaluationError, ValueError):
    pass


class KpiDependencyNotRequestedError(KpiEvaluationError, KeyError):
    pass
