# Espejo pedagógico: conserva exactamente el código productivo y agrega sólo comentarios.
# La composición entrega settings ya resueltos; este módulo no interpreta variables de entorno.
"""Errores públicos sanitizados para las operaciones de Cosmos DB."""

from __future__ import annotations

from typing import Any


# Contrato/clase CosmosError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosError(RuntimeError):
    """Error base del conector Cosmos."""


# Contrato/clase CosmosConfigurationError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosConfigurationError(CosmosError, ValueError):
    """La configuración o un argumento público no cumple el contrato."""


# Contrato/clase CosmosOperationError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosOperationError(CosmosError):
    """Cosmos rechazó o no pudo completar una operación."""


# Contrato/clase CosmosClosedError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosClosedError(CosmosOperationError):
    """El cliente ya fue cerrado y no puede volver a utilizarse."""


# Contrato/clase CosmosAuthenticationError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosAuthenticationError(CosmosOperationError):
    """Las credenciales no pudieron autenticar la solicitud."""


# Contrato/clase CosmosAuthorizationError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosAuthorizationError(CosmosOperationError):
    """La identidad o clave no está autorizada para la operación."""


# Contrato/clase CosmosDatabaseNotFoundError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosDatabaseNotFoundError(CosmosOperationError):
    """La base configurada no existe."""


# Contrato/clase CosmosContainerNotFoundError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosContainerNotFoundError(CosmosOperationError):
    """El contenedor solicitado no existe."""


# Contrato/clase CosmosItemNotFoundError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosItemNotFoundError(CosmosOperationError):
    """El documento solicitado no existe."""


# Contrato/clase CosmosConflictError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosConflictError(CosmosOperationError):
    """La operación entra en conflicto con un recurso existente."""


# Contrato/clase CosmosPreconditionFailedError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosPreconditionFailedError(CosmosOperationError):
    """El ETag entregado ya no representa la versión vigente."""


# Contrato/clase CosmosThrottledError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosThrottledError(CosmosOperationError):
    """Cosmos agotó los reintentos seguros de throttling del SDK."""


# Contrato/clase CosmosQueryContractError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosQueryContractError(CosmosConfigurationError):
    """La consulta no declara un alcance o formato válido."""


# Contrato/clase CosmosResultLimitError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosResultLimitError(CosmosOperationError):
    """Una consulta materializada superó el máximo autorizado."""

    # Helper interno __init__: valida o adapta datos antes de tocar el SDK.
    def __init__(self, *, max_items: int) -> None:
        self.max_items = max_items
        super().__init__(f'Cosmos query exceeded max_items={max_items}')


# Contrato/clase CosmosProvisioningError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosProvisioningError(CosmosOperationError):
    """El aprovisionamiento explícito no pudo completarse."""


# Contrato/clase CosmosContainerDefinitionMismatchError: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosContainerDefinitionMismatchError(CosmosProvisioningError):
    """Un contenedor existente no coincide con su definición aprobada."""

    # Helper interno __init__: valida o adapta datos antes de tocar el SDK.
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
