# Error base de la composición NOTPII específica de ADA.
class NotPiiProcessError(RuntimeError):
    pass


# Error de configuración del proceso; también se comporta como ValueError.
class NotPiiProcessConfigurationError(NotPiiProcessError, ValueError):
    pass


# Error del catálogo concreto que ADA entrega al producer NOTPII.
class NotPiiCatalogError(ValueError):
    pass
