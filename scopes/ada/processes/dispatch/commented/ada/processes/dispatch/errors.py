# Agrupa errores propios del proceso para separar fallos de dominio e infraestructura.
class DispatchProcessError(RuntimeError):
    pass


class DispatchProcessConfigurationError(DispatchProcessError):
    pass


class DispatchCatalogError(ValueError):
    pass


class DispatchSqlReadError(DispatchProcessError):
    pass


class DispatchSchemaError(DispatchProcessError):
    pass


class DispatchMaterializationError(DispatchProcessError):
    pass
