class PiWebApiProcessError(RuntimeError):
    pass


class PiWebApiProcessConfigurationError(PiWebApiProcessError, ValueError):
    pass


class PiWebApiCatalogError(ValueError):
    pass
