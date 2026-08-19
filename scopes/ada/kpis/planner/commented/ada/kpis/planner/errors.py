# Espejo pedagógico: conserva exactamente el contrato ejecutable y añade contexto en español.
# Los errores de lookup se distinguen de errores de definición del catálogo.
class KpiPlannerError(Exception):
    pass


class KpiPlanKeyError(KpiPlannerError, KeyError):
    pass
