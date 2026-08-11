"""Conectividad de solo lectura con Azure Key Vault para Atlanticus."""

from pkgutil import extend_path

from atlanticus.connectivity.key_vault.client import KeyVaultClient
from atlanticus.connectivity.key_vault.errors import (
    KeyVaultAuthenticationError,
    KeyVaultAuthorizationError,
    KeyVaultClosedError,
    KeyVaultConfigurationError,
    KeyVaultError,
    KeyVaultOperationError,
    KeyVaultSecretNotFoundError,
    KeyVaultSecretValueError,
)
from atlanticus.connectivity.key_vault.settings import KeyVaultSettings

__path__ = extend_path(__path__, __name__)

__version__ = '0.1.0'

__all__ = [
    'KeyVaultAuthenticationError',
    'KeyVaultAuthorizationError',
    'KeyVaultClient',
    'KeyVaultClosedError',
    'KeyVaultConfigurationError',
    'KeyVaultError',
    'KeyVaultOperationError',
    'KeyVaultSecretNotFoundError',
    'KeyVaultSecretValueError',
    'KeyVaultSettings',
    '__version__',
]
