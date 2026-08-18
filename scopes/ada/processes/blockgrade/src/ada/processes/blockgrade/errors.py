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
