"""Primitivas pequeñas y sin dependencias para aplicaciones Atlanticus."""

# Este archivo define la API pública del wheel. Los consumidores deberían importar desde
# ``atlanticus.kernel`` y no depender de la organización de los módulos internos.
from atlanticus.kernel.environment import ENVIRONMENT_VARIABLE, Environment, EnvironmentName
from atlanticus.kernel.errors import InvalidEnvironmentError, KernelError
from atlanticus.kernel.sanitization import REDACTED, DataSanitizer
from atlanticus.kernel.status import OperationStatus
from atlanticus.kernel.time import utc_now

# La versión se mantiene junto a la API para poder consultarla sin cargar herramientas de build.
__version__ = '0.1.0'

# ``__all__`` hace explícito el contrato público. Un nombre interno que no aparezca en esta lista
# puede cambiar sin que se considere parte estable del wheel.
__all__ = [
    'ENVIRONMENT_VARIABLE',
    'REDACTED',
    'DataSanitizer',
    'Environment',
    'EnvironmentName',
    'InvalidEnvironmentError',
    'KernelError',
    'OperationStatus',
    '__version__',
    'utc_now',
]
