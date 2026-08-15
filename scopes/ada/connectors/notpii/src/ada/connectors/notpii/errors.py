class NotPiiConnectorError(RuntimeError):
    pass


class NotPiiConfigurationError(NotPiiConnectorError, ValueError):
    pass


class NotPiiSourceError(NotPiiConnectorError):
    pass
