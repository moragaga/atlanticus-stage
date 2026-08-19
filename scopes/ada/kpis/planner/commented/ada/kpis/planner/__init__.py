# Espejo pedagógico: conserva exactamente el contrato ejecutable y añade contexto en español.
# La API pública expone únicamente contratos de planificación reutilizables.
from ada.kpis.planner.errors import KpiPlanKeyError, KpiPlannerError
from ada.kpis.planner.models import KpiLoadPlan, KpiSourceLoadPlan
from ada.kpis.planner.planner import KpiRequirementPlanner

__version__ = '0.1.0'

__all__ = [
    'KpiLoadPlan',
    'KpiPlanKeyError',
    'KpiPlannerError',
    'KpiRequirementPlanner',
    'KpiSourceLoadPlan',
    '__version__',
]
