# API pública del capability de evaluación KPI.
# Solo reexporta contratos reutilizables; no compone jobs ni persistencia.
from ada.kpis.evaluation.dependencies import KpiDependencies
from ada.kpis.evaluation.errors import (
    KpiDependencyNotRequestedError,
    KpiEvaluationError,
    KpiInvalidValueError,
)
from ada.kpis.evaluation.evaluator import KpiEvaluationSourceLoader, KpiEvaluator

__version__ = '0.1.0'

__all__ = [
    'KpiDependencies',
    'KpiDependencyNotRequestedError',
    'KpiEvaluationError',
    'KpiEvaluationSourceLoader',
    'KpiEvaluator',
    'KpiInvalidValueError',
    '__version__',
]
