from pkgutil import extend_path

from ada.processes.pi_web_api.acquisition import PiStreamSetAcquirer
from ada.processes.pi_web_api.bootstrap import load_configuration, run
from ada.processes.pi_web_api.catalog import build_catalog
from ada.processes.pi_web_api.composition import (
    PI_WEB_API_JOB_DEFINITION,
    PiWebApiComposition,
    build_composition,
)
from ada.processes.pi_web_api.errors import (
    PiWebApiAcquisitionError,
    PiWebApiCatalogError,
    PiWebApiMaterializationError,
    PiWebApiPlannerError,
    PiWebApiProcessConfigurationError,
    PiWebApiProcessError,
    PiWebApiTimeoutExhaustedError,
    PiWebApiWatermarkError,
    PiWebApiWebIdRegistryError,
)
from ada.processes.pi_web_api.job import PiWebApiJob
from ada.processes.pi_web_api.materialization import PiWebApiMaterializer
from ada.processes.pi_web_api.models import (
    PiAcquisitionResult,
    PiAcquisitionWindow,
    PiExecutionPlan,
    PiMaterializationResult,
    PiPreparationResult,
    PiSample,
    ResolvedPiTag,
)
from ada.processes.pi_web_api.planning import PiSlotPlanner
from ada.processes.pi_web_api.preparation import PiExecutionPlanPreparer
from ada.processes.pi_web_api.settings import PiWebApiProcessSettings, configuration_specs
from ada.processes.pi_web_api.watermarks import (
    PiProducerState,
    PiProducerWatermark,
    PiSourceState,
    PiSourceWatermark,
    PiWatermarkCoordinator,
)
from ada.processes.pi_web_api.web_ids import WebIdRegistry

__path__ = extend_path(__path__, __name__)

__all__ = [
    'PI_WEB_API_JOB_DEFINITION',
    'PiAcquisitionResult',
    'PiAcquisitionWindow',
    'PiExecutionPlan',
    'PiMaterializationResult',
    'PiExecutionPlanPreparer',
    'PiPreparationResult',
    'PiSample',
    'PiProducerState',
    'PiProducerWatermark',
    'PiSlotPlanner',
    'PiStreamSetAcquirer',
    'PiSourceState',
    'PiSourceWatermark',
    'PiWatermarkCoordinator',
    'PiWebApiAcquisitionError',
    'PiWebApiCatalogError',
    'PiWebApiComposition',
    'PiWebApiJob',
    'PiWebApiMaterializationError',
    'PiWebApiMaterializer',
    'PiWebApiPlannerError',
    'PiWebApiProcessConfigurationError',
    'PiWebApiProcessError',
    'PiWebApiProcessSettings',
    'PiWebApiTimeoutExhaustedError',
    'PiWebApiWatermarkError',
    'PiWebApiWebIdRegistryError',
    'ResolvedPiTag',
    'WebIdRegistry',
    'build_catalog',
    'build_composition',
    'configuration_specs',
    'load_configuration',
    'run',
]
