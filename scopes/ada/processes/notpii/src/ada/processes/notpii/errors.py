class NotPiiProcessError(RuntimeError):
    pass


class NotPiiProcessConfigurationError(NotPiiProcessError, ValueError):
    pass


class NotPiiCatalogError(NotPiiProcessError, ValueError):
    pass


class NotPiiMaterializationError(NotPiiProcessError):
    pass
