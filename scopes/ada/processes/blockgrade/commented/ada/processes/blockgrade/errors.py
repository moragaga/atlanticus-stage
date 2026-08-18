# Error base exclusivo de la composición Blockgrade de ADA.
class BlockgradeProcessError(RuntimeError):
    pass


# Error de configuración resuelta para este proceso concreto.
class BlockgradeProcessConfigurationError(BlockgradeProcessError):
    pass


# Error de definición del catálogo concreto de Blockgrade.
class BlockgradeCatalogError(ValueError):
    pass
