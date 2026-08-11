"""Primitivas pequeñas y sin dependencias para aplicaciones Atlanticus."""

from atlanticus.kernel.environment import ENVIRONMENT_VARIABLE, Environment, EnvironmentName
from atlanticus.kernel.errors import InvalidEnvironmentError, KernelError
from atlanticus.kernel.sanitization import REDACTED, DataSanitizer
from atlanticus.kernel.status import OperationStatus
from atlanticus.kernel.time import utc_now

__version__ = '0.1.0'

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
