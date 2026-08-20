# Errores propios de Delivery: mantienen la frontera del módulo sin filtrar detalles de infraestructura.
class KpiDeliveryError(Exception):
    pass


class KpiDeliveryValidationError(KpiDeliveryError):
    pass
