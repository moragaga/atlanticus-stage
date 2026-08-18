# Error base de la capacidad reusable NOTPII.
class NotPiiDataProducerError(RuntimeError):
    pass


# Error de configuración del producer.
class NotPiiDataProducerConfigurationError(NotPiiDataProducerError, ValueError):
    pass


# Error del contrato de catálogo esperado por el producer.
class NotPiiCatalogError(NotPiiDataProducerError, ValueError):
    pass


# Error de publicación/materialización.
class NotPiiMaterializationError(NotPiiDataProducerError):
    pass


# Error asociado al contenido de una fuente o mensaje NOTPII.
class NotPiiSourceError(NotPiiDataProducerError):
    pass
