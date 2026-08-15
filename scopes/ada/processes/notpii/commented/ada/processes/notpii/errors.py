# Espejo comentado del proceso NOTPII: composición, batch, materialización, estado y settlement.
# Este archivo conserva exactamente el comportamiento productivo y agrega solo contexto.
class NotPiiProcessError(RuntimeError):
    pass


class NotPiiProcessConfigurationError(NotPiiProcessError, ValueError):
    pass


class NotPiiCatalogError(NotPiiProcessError, ValueError):
    pass


class NotPiiMaterializationError(NotPiiProcessError):
    pass
