# Espejo comentado de la API pública del proceso NOTPII.
from pkgutil import extend_path

from ada.processes.notpii.bootstrap import load_configuration, run
from ada.processes.notpii.catalog import build_catalog
from ada.processes.notpii.composition import (
    NOTPII_JOB_DEFINITION,
    NotPiiComposition,
    build_composition,
)
from ada.processes.notpii.errors import (
    NotPiiCatalogError,
    NotPiiMaterializationError,
    NotPiiProcessConfigurationError,
    NotPiiProcessError,
)
from ada.processes.notpii.job import NotPiiJob
from ada.processes.notpii.materialization import NotPiiMaterializer
from ada.processes.notpii.models import NotPiiProcessingResult
from ada.processes.notpii.processor import NotPiiProcessor
from ada.processes.notpii.producer_state import (
    NotPiiProducerManifest,
    NotPiiProducerState,
    NotPiiStreamObservation,
    NotPiiStreamState,
)
from ada.processes.notpii.settings import NotPiiSettings, configuration_specs

__path__ = extend_path(__path__, __name__)

__version__ = '0.1.0'

__all__ = [
    'NOTPII_JOB_DEFINITION',
    'NotPiiCatalogError',
    'NotPiiComposition',
    'NotPiiJob',
    'NotPiiMaterializationError',
    'NotPiiMaterializer',
    'NotPiiProcessConfigurationError',
    'NotPiiProcessError',
    'NotPiiProcessingResult',
    'NotPiiProcessor',
    'NotPiiProducerManifest',
    'NotPiiProducerState',
    'NotPiiSettings',
    'NotPiiStreamObservation',
    'NotPiiStreamState',
    '__version__',
    'build_catalog',
    'build_composition',
    'configuration_specs',
    'load_configuration',
    'run',
]
