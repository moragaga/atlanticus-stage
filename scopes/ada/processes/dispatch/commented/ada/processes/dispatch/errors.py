# Error base exclusivo de la composición Dispatch de ADA.
class DispatchProcessError(RuntimeError):
    pass


# Error de configuración resuelta para este proceso concreto.
class DispatchProcessConfigurationError(DispatchProcessError):
    pass


# Error de definición del catálogo concreto de Dispatch.
class DispatchCatalogError(ValueError):
    pass
