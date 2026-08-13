"""Errores públicos sanitizados para las operaciones de Cosmos DB."""

from __future__ import annotations

from typing import Any


class CosmosError(RuntimeError):
    """Error base del conector Cosmos."""


class CosmosConfigurationError(CosmosError, ValueError):
    """La configuración o un argumento público no cumple el contrato."""


class CosmosOperationError(CosmosError):
    """Cosmos rechazó o no pudo completar una operación."""


class CosmosClosedError(CosmosOperationError):
    """El cliente ya fue cerrado y no puede volver a utilizarse."""


class CosmosAuthenticationError(CosmosOperationError):
    """Las credenciales no pudieron autenticar la solicitud."""


class CosmosAuthorizationError(CosmosOperationError):
    """La identidad o clave no está autorizada para la operación."""


class CosmosDatabaseNotFoundError(CosmosOperationError):
    """La base configurada no existe."""


class CosmosContainerNotFoundError(CosmosOperationError):
    """El contenedor solicitado no existe."""


class CosmosItemNotFoundError(CosmosOperationError):
    """El documento solicitado no existe."""


class CosmosConflictError(CosmosOperationError):
    """La operación entra en conflicto con un recurso existente."""


class CosmosPreconditionFailedError(CosmosOperationError):
    """El ETag entregado ya no representa la versión vigente."""


class CosmosThrottledError(CosmosOperationError):
    """Cosmos agotó los reintentos seguros de throttling del SDK."""


class CosmosQueryContractError(CosmosConfigurationError):
    """La consulta no declara un alcance o formato válido."""


class CosmosResultLimitError(CosmosOperationError):
    """Una consulta materializada superó el máximo autorizado."""

    def __init__(self, *, max_items: int) -> None:
        self.max_items = max_items
        super().__init__(f'Cosmos query exceeded max_items={max_items}')


class CosmosProvisioningError(CosmosOperationError):
    """El aprovisionamiento explícito no pudo completarse."""


class CosmosContainerDefinitionMismatchError(CosmosProvisioningError):
    """Un contenedor existente no coincide con su definición aprobada."""

    def __init__(
        self,
        *,
        container_name: str,
        property_name: str,
        expected: Any,
        actual: Any,
    ) -> None:
        self.container_name = container_name
        self.property_name = property_name
        self.expected = expected
        self.actual = actual
        super().__init__(
            f'Cosmos container {container_name!r} has incompatible {property_name}: '
            f'expected={expected!r}, actual={actual!r}'
        )
