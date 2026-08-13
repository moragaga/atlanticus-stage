"""Errores públicos y sanitizados del conector Service Bus."""
# Espejo pedagógico: conserva exactamente el contrato ejecutable y agrega contexto de diseño.

from __future__ import annotations


# La jerarquía evita propagar mensajes del SDK que podrían contener datos sensibles.
class ServiceBusError(Exception):
    """Error base de ``atlanticus-service-bus``."""


class ServiceBusConfigurationError(ServiceBusError):
    """Indica una configuración ausente o inválida."""


class ServiceBusAuthenticationError(ServiceBusError):
    """Indica que Service Bus rechazó la autenticación."""


class ServiceBusAuthorizationError(ServiceBusError):
    """Indica que la identidad no tiene permisos suficientes."""


class ServiceBusConnectionError(ServiceBusError):
    """Indica un fallo al abrir, usar o cerrar la conexión."""


class ServiceBusReceiveError(ServiceBusError):
    """Representa un fallo al recibir una entrega."""


class ServiceBusMessageError(ServiceBusError):
    """Indica que una entrega no pudo convertirse al modelo neutral."""


class ServiceBusSettlementError(ServiceBusError):
    """Representa un fallo al resolver o renovar una entrega."""
