class KpiProcessError(Exception):
    pass


class KpiProcessConfigurationError(KpiProcessError):
    pass


class KpiProcessCatalogError(KpiProcessError):
    pass


class KpiProcessWatermarkError(KpiProcessError):
    pass
