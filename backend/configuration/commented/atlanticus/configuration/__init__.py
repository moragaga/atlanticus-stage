# Espejo pedagógico del módulo productivo de configuración.
# Conserva exactamente su comportamiento y agrega contexto para mantenimiento.
"""Bootstrap de configuración seguro para procesos backend Atlanticus."""

from atlanticus.configuration.bootstrap import ConfigurationBootstrap
from atlanticus.configuration.contracts import SecretResolver
from atlanticus.configuration.errors import (
    ConfigurationError,
    ConfigurationSourceError,
    ConfigurationValueError,
    MissingConfigurationVariablesError,
    SecretResolutionError,
    SecretsManifestError,
)
from atlanticus.configuration.manifest import SecretManifestEntry, SecretsManifest
from atlanticus.configuration.models import (
    ConfigurationSource,
    ConfigurationVariableSpec,
    ResolvedConfiguration,
)

__version__ = '0.1.0'

__all__ = [
    'ConfigurationBootstrap',
    'ConfigurationError',
    'ConfigurationSource',
    'ConfigurationSourceError',
    'ConfigurationValueError',
    'ConfigurationVariableSpec',
    'MissingConfigurationVariablesError',
    'ResolvedConfiguration',
    'SecretManifestEntry',
    'SecretResolutionError',
    'SecretResolver',
    'SecretsManifest',
    'SecretsManifestError',
    '__version__',
]
