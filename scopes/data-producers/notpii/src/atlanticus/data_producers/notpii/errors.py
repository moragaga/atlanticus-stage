class NotPiiDataProducerError(RuntimeError):
    pass


class NotPiiDataProducerConfigurationError(NotPiiDataProducerError, ValueError):
    pass


class NotPiiCatalogError(NotPiiDataProducerError, ValueError):
    pass


class NotPiiMaterializationError(NotPiiDataProducerError):
    pass


class NotPiiSourceError(NotPiiDataProducerError):
    pass
