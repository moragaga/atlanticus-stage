# Espejo comentado del conector NOTPII: adquisición Service Bus/Storage sin lógica adicional.
# Este archivo conserva exactamente el comportamiento productivo y agrega solo contexto.
class NotPiiConnectorError(RuntimeError):
    pass


class NotPiiConfigurationError(NotPiiConnectorError, ValueError):
    pass


class NotPiiSourceError(NotPiiConnectorError):
    pass
