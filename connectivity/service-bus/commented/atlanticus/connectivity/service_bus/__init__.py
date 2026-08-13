"""Conectividad genérica y síncrona para Azure Service Bus."""
# Espejo pedagógico: conserva exactamente el contrato ejecutable y agrega contexto de diseño.

from pkgutil import extend_path

from atlanticus.connectivity.service_bus.delivery import ServiceBusDelivery
from atlanticus.connectivity.service_bus.errors import (
    ServiceBusAuthenticationError,
    ServiceBusAuthorizationError,
    ServiceBusConfigurationError,
    ServiceBusConnectionError,
    ServiceBusError,
    ServiceBusMessageError,
    ServiceBusReceiveError,
    ServiceBusSettlementError,
)
from atlanticus.connectivity.service_bus.models import ServiceBusDeliveryState, ServiceBusMessage
from atlanticus.connectivity.service_bus.receiver import ServiceBusTopicReceiver
from atlanticus.connectivity.service_bus.settings import ServiceBusSettings

__path__ = extend_path(__path__, __name__)

__version__ = '0.1.0'

# La fachada expone únicamente el contrato estable y no publica modos configurables.
__all__ = [
    'ServiceBusAuthenticationError',
    'ServiceBusAuthorizationError',
    'ServiceBusConfigurationError',
    'ServiceBusConnectionError',
    'ServiceBusDelivery',
    'ServiceBusDeliveryState',
    'ServiceBusError',
    'ServiceBusMessage',
    'ServiceBusMessageError',
    'ServiceBusReceiveError',
    'ServiceBusSettings',
    'ServiceBusSettlementError',
    'ServiceBusTopicReceiver',
    '__version__',
]
