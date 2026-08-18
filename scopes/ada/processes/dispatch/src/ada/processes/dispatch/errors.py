class DispatchProcessError(RuntimeError):
    pass


class DispatchProcessConfigurationError(DispatchProcessError):
    pass


class DispatchCatalogError(ValueError):
    pass
