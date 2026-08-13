# Espejo pedagógico: este archivo conserva exactamente el código ejecutable del módulo
# productivo y añade sólo comentarios para explicar sus límites y responsabilidades.
"""Conectividad Azure Blob Storage neutral y síncrona para Atlanticus."""

from pkgutil import extend_path

from atlanticus.connectivity.storage.client import StorageClient
from atlanticus.connectivity.storage.errors import (
    StorageAuthenticationError,
    StorageAuthorizationError,
    StorageBlobNotFoundError,
    StorageClosedError,
    StorageConfigurationError,
    StorageConflictError,
    StorageConnectionError,
    StorageContainerNotFoundError,
    StorageError,
    StorageOperationError,
    StorageResultLimitError,
)
from atlanticus.connectivity.storage.models import StorageBlobProperties
from atlanticus.connectivity.storage.settings import (
    DEFAULT_STORAGE_CONNECTION_TIMEOUT_SECONDS,
    DEFAULT_STORAGE_MAX_LIST_ITEMS,
    DEFAULT_STORAGE_READ_TIMEOUT_SECONDS,
    StorageConnectionStringCredential,
    StorageCredential,
    StorageSasCredential,
    StorageSettings,
    sanitize_account_url,
)

__path__ = extend_path(__path__, __name__)

__version__ = '0.1.0'

# Sólo se exportan contratos Atlanticus. BlobServiceClient, ContentSettings y excepciones Azure
# quedan intencionalmente detrás de la frontera del módulo.
__all__ = [
    'DEFAULT_STORAGE_CONNECTION_TIMEOUT_SECONDS',
    'DEFAULT_STORAGE_MAX_LIST_ITEMS',
    'DEFAULT_STORAGE_READ_TIMEOUT_SECONDS',
    'StorageAuthenticationError',
    'StorageAuthorizationError',
    'StorageBlobNotFoundError',
    'StorageBlobProperties',
    'StorageClient',
    'StorageClosedError',
    'StorageConfigurationError',
    'StorageConflictError',
    'StorageConnectionError',
    'StorageConnectionStringCredential',
    'StorageContainerNotFoundError',
    'StorageCredential',
    'StorageError',
    'StorageOperationError',
    'StorageResultLimitError',
    'StorageSasCredential',
    'StorageSettings',
    '__version__',
    'sanitize_account_url',
]
