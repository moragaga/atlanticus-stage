# Espejo pedagógico: este archivo conserva exactamente el código ejecutable del módulo
# productivo y añade sólo comentarios para explicar sus límites y responsabilidades.
"""Errores públicos y seguros del conector Azure Blob Storage."""


# La jerarquía pública traduce errores Azure a categorías estables y evita propagar URLs, SAS o
# detalles internos del transporte hacia logs y consumidores.
class StorageError(Exception):
    """Error base de ``atlanticus-storage``."""


class StorageConfigurationError(StorageError):
    """Indica una configuración ausente o inválida."""


class StorageClosedError(StorageError):
    """Indica que el cliente fue cerrado definitivamente."""


class StorageConnectionError(StorageError):
    """Representa un fallo de conexión o cierre sin exponer credenciales."""


class StorageAuthenticationError(StorageError):
    """Indica que Azure Storage rechazó la credencial."""


class StorageAuthorizationError(StorageError):
    """Indica que la credencial no posee permisos para la operación."""


class StorageContainerNotFoundError(StorageError):
    """Indica que el container solicitado no existe."""


class StorageBlobNotFoundError(StorageError):
    """Indica que el blob solicitado no existe."""


class StorageConflictError(StorageError):
    """Indica un conflicto de estado reportado por Azure Storage."""


class StorageOperationError(StorageError):
    """Representa un fallo remoto no clasificado sin filtrar detalles sensibles."""


class StorageResultLimitError(StorageError):
    """Indica que un listado excedió el límite configurado."""

    def __init__(self, *, max_items: int) -> None:
        self.max_items = max_items
        super().__init__(f'Storage listing exceeds the limit of {max_items} items')
