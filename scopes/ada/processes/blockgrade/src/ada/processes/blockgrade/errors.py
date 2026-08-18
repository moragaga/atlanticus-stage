class BlockgradeProcessError(RuntimeError):
    pass


class BlockgradeProcessConfigurationError(BlockgradeProcessError):
    pass


class BlockgradeCatalogError(ValueError):
    pass
