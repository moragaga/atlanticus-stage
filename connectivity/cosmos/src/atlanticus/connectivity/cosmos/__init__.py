"""Conectividad Azure Cosmos DB neutral y síncrona para Atlanticus."""

from pkgutil import extend_path

from atlanticus.connectivity.cosmos.client import CosmosClient
from atlanticus.connectivity.cosmos.errors import (
    CosmosAuthenticationError,
    CosmosAuthorizationError,
    CosmosClosedError,
    CosmosConfigurationError,
    CosmosConflictError,
    CosmosContainerDefinitionMismatchError,
    CosmosContainerNotFoundError,
    CosmosDatabaseNotFoundError,
    CosmosError,
    CosmosItemNotFoundError,
    CosmosOperationError,
    CosmosPreconditionFailedError,
    CosmosProvisioningError,
    CosmosQueryContractError,
    CosmosResultLimitError,
    CosmosThrottledError,
)
from atlanticus.connectivity.cosmos.models import (
    CosmosContainerSpec,
    CosmosPage,
    CosmosPatchOperation,
    CosmosQueryParameter,
)
from atlanticus.connectivity.cosmos.provisioner import CosmosProvisioner
from atlanticus.connectivity.cosmos.settings import (
    DEFAULT_COSMOS_CONNECTION_TIMEOUT_SECONDS,
    DEFAULT_COSMOS_MAX_QUERY_ITEMS,
    DEFAULT_COSMOS_PAGE_SIZE,
    DEFAULT_COSMOS_REQUEST_TIMEOUT_SECONDS,
    CosmosSettings,
    sanitize_endpoint,
)

__path__ = extend_path(__path__, __name__)

__version__ = '0.1.0'

__all__ = [
    'DEFAULT_COSMOS_CONNECTION_TIMEOUT_SECONDS',
    'DEFAULT_COSMOS_MAX_QUERY_ITEMS',
    'DEFAULT_COSMOS_PAGE_SIZE',
    'DEFAULT_COSMOS_REQUEST_TIMEOUT_SECONDS',
    'CosmosAuthenticationError',
    'CosmosAuthorizationError',
    'CosmosClosedError',
    'CosmosClient',
    'CosmosConfigurationError',
    'CosmosConflictError',
    'CosmosContainerDefinitionMismatchError',
    'CosmosContainerNotFoundError',
    'CosmosContainerSpec',
    'CosmosDatabaseNotFoundError',
    'CosmosError',
    'CosmosItemNotFoundError',
    'CosmosOperationError',
    'CosmosPage',
    'CosmosPatchOperation',
    'CosmosPreconditionFailedError',
    'CosmosProvisioner',
    'CosmosProvisioningError',
    'CosmosQueryContractError',
    'CosmosQueryParameter',
    'CosmosResultLimitError',
    'CosmosSettings',
    'CosmosThrottledError',
    '__version__',
    'sanitize_endpoint',
]
