# Fachada de compatibilidad hacia el productor NOT PII global.
from atlanticus.data_producers.notpii.connector import NotPiiConnector, decode_message

__all__ = ['NotPiiConnector', 'decode_message']
