# Fachada de compatibilidad hacia el productor NOT PII global.
from atlanticus.data_producers.notpii import (
    NotPiiBatch,
    NotPiiBlobMessage,
    NotPiiConfigurationError,
    NotPiiConnector,
    NotPiiConnectorError,
    NotPiiSourceError,
    decode_message,
)

__version__ = '0.1.0'

__all__ = [
    'NotPiiBatch',
    'NotPiiBlobMessage',
    'NotPiiConfigurationError',
    'NotPiiConnector',
    'NotPiiConnectorError',
    'NotPiiSourceError',
    '__version__',
    'decode_message',
]
