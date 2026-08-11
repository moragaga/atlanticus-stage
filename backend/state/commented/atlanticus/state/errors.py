# Los errores de state se propagan: perder el checkpoint no es un fallo ignorable.
"""Errores públicos del almacenamiento de estado."""

from __future__ import annotations


class StateError(Exception):
    """Base para errores que deben ser manejados explícitamente por el job."""


# Un valor inválido se distingue de I/O para que el job corrija su contrato.
class StateValidationError(StateError, ValueError):
    """El contrato recibido no puede representarse como estado válido."""


# Leer y escribir son fallos técnicos diferentes y permiten decisiones operacionales distintas.
class StateReadError(StateError, OSError):
    """El documento no pudo leerse desde el almacenamiento."""


class StateWriteError(StateError, OSError):
    """El documento no pudo confirmarse atómicamente."""


# Un archivo presente pero ilegible nunca se interpreta como “todavía no existe”.
class StateCorruptionError(StateError):
    """El contenido persistido no corresponde a un documento legible."""


class StateSchemaError(StateCorruptionError):
    """La versión persistida no es compatible con esta biblioteca."""


class StateTooLargeError(StateError):
    """El documento supera el límite de seguridad configurado."""
