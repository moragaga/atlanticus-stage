# La fachada pública expone únicamente los contratos que consumen los procesos.
# El código bajo estos comentarios es equivalente al productivo y conserva el mismo comportamiento.

"""Ejecución controlada y coordinación de jobs backend Atlanticus."""

from atlanticus.runtime.configuration import RuntimeConfiguration
from atlanticus.runtime.context import JobRuntimeContext
from atlanticus.runtime.definition import JobDefinition
from atlanticus.runtime.errors import (
    AtlanticusRuntimeError,
    ConcurrentExecutionError,
    LeaseOwnershipLostError,
    LeaseRenewalError,
    RuntimeCancellationRequested,
    RuntimeConfigurationError,
    RuntimeContractError,
)
from atlanticus.runtime.runner import RuntimeExecutionResult, execute_job

__version__ = '0.5.0'

__all__ = [
    'AtlanticusRuntimeError',
    'ConcurrentExecutionError',
    'JobDefinition',
    'JobRuntimeContext',
    'LeaseOwnershipLostError',
    'LeaseRenewalError',
    'RuntimeCancellationRequested',
    'RuntimeConfiguration',
    'RuntimeConfigurationError',
    'RuntimeContractError',
    'RuntimeExecutionResult',
    '__version__',
    'execute_job',
]
