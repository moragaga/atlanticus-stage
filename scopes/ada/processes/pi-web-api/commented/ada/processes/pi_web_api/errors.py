# Error base de la composición PI Web API específica de ADA.
class PiWebApiProcessError(RuntimeError):
    pass


# Error de configuración resuelta para el proceso PI Web API.
class PiWebApiProcessConfigurationError(PiWebApiProcessError, ValueError):
    pass


# Error del catálogo concreto que ADA entrega al producer PI.
class PiWebApiCatalogError(ValueError):
    pass
