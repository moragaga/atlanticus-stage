# Proceso Series: lee Historian, selecciona timestamps exactos y publica una proyección compacta.
# Agrupa errores propios de esta frontera sin filtrar detalles de infraestructura.

# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesDeliveryError(Exception):
    pass


# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesDeliveryConfigurationError(KpiTimeseriesDeliveryError):
    pass


# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesDeliveryRepositoryError(KpiTimeseriesDeliveryError):
    pass


# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesDeliveryHistoryError(KpiTimeseriesDeliveryError):
    pass
