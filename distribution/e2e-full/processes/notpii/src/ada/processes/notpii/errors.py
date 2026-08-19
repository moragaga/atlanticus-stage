class NotPiiProcessError(RuntimeError):
    pass


class NotPiiProcessConfigurationError(NotPiiProcessError, ValueError):
    pass


class NotPiiCatalogError(ValueError):
    pass
