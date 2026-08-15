# Espejo comentado del conector NOTPII: adquisición Service Bus/Storage sin lógica adicional.
# Este archivo conserva exactamente el comportamiento productivo y agrega solo contexto.
from ada.connectors.notpii.connector import NotPiiConnector, decode_message
from ada.connectors.notpii.errors import (
    NotPiiConfigurationError,
    NotPiiConnectorError,
    NotPiiSourceError,
)
from ada.connectors.notpii.models import NotPiiBatch, NotPiiBlobMessage

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
