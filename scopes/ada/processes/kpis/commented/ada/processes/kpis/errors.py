# Errores propios del proceso; no mezcla errores de las capabilities reutilizables.
class KpiProcessError(Exception):
    pass


class KpiProcessConfigurationError(KpiProcessError):
    pass


class KpiProcessCatalogError(KpiProcessError):
    pass


class KpiProcessWatermarkError(KpiProcessError):
    pass
