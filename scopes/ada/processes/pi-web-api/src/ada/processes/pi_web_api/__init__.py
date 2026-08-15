from pkgutil import extend_path

from ada.processes.pi_web_api.bootstrap import load_configuration, run
from ada.processes.pi_web_api.catalog import build_catalog
from ada.processes.pi_web_api.composition import (
    PI_WEB_API_JOB_DEFINITION,
    PiWebApiComposition,
    build_composition,
)
from ada.processes.pi_web_api.errors import (
    PiWebApiCatalogError,
    PiWebApiPlannerError,
    PiWebApiProcessConfigurationError,
    PiWebApiProcessError,
    PiWebApiWatermarkError,
    PiWebApiWebIdRegistryError,
)
from ada.processes.pi_web_api.job import PiWebApiJob
from ada.processes.pi_web_api.models import (
    PiAcquisitionWindow,
    PiExecutionPlan,
    PiPreparationResult,
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
    'PiAcquisitionWindow',
    'PiExecutionPlan',
    'PiExecutionPlanPreparer',
    'PiPreparationResult',
    'PiProducerState',
    'PiProducerWatermark',
    'PiSlotPlanner',
    'PiSourceState',
    'PiSourceWatermark',
    'PiWatermarkCoordinator',
    'PiWebApiCatalogError',
    'PiWebApiComposition',
    'PiWebApiJob',
    'PiWebApiPlannerError',
    'PiWebApiProcessConfigurationError',
    'PiWebApiProcessError',
    'PiWebApiProcessSettings',
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
