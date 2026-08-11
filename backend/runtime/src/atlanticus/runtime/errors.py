"""Errores públicos del contrato de ejecución de jobs."""

import re


class AtlanticusRuntimeError(Exception):
    """Base de los errores controlados por job-runtime."""


class RuntimeConfigurationError(AtlanticusRuntimeError):
    """Indica que faltan variables o que su combinación es inválida."""


class RuntimeContractError(AtlanticusRuntimeError):
    """Indica una definición de job incoherente."""


class ConcurrentExecutionError(AtlanticusRuntimeError):
    """Indica que otro contenedor conserva la lease del servicio."""


class RuntimeCancellationRequested(AtlanticusRuntimeError):
    """Permite que una iteración abandone trabajo de forma cooperativa."""

    def __init__(self, reason: str = 'cancellation_requested') -> None:
        if not isinstance(reason, str):
            raise TypeError('reason must be a string')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', reason):
            raise ValueError('reason must use lower snake_case')
        super().__init__(reason)
        self.reason = reason


class LeaseOwnershipLostError(AtlanticusRuntimeError):
    """Indica que el proceso dejó de ser propietario de su lease."""


class LeaseRenewalError(AtlanticusRuntimeError):
    """Indica que el heartbeat no pudo confirmar una renovación segura."""
