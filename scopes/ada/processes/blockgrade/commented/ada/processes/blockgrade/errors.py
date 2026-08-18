# Agrupa errores propios del proceso para separar fallos de dominio e infraestructura.
class BlockgradeProcessError(RuntimeError):
    pass


class BlockgradeProcessConfigurationError(BlockgradeProcessError):
    pass


class BlockgradeCatalogError(ValueError):
    pass


class BlockgradeSqlReadError(BlockgradeProcessError):
    pass


class BlockgradeSchemaError(BlockgradeProcessError):
    pass


class BlockgradeMaterializationError(BlockgradeProcessError):
    pass
