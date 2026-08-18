# Espejo comentado del proceso ADA PI Web API: errors.py
from atlanticus.data_producers.pi.errors import (
    PiDataProducerAcquisitionError,
    PiDataProducerCatalogError,
    PiDataProducerError,
    PiDataProducerMaterializationError,
    PiDataProducerPlannerError,
    PiDataProducerTimeoutExhaustedError,
    PiDataProducerWatermarkError,
    PiDataProducerWebIdRegistryError,
)

PiWebApiProcessError = PiDataProducerError
PiWebApiCatalogError = PiDataProducerCatalogError
PiWebApiWebIdRegistryError = PiDataProducerWebIdRegistryError
PiWebApiWatermarkError = PiDataProducerWatermarkError
PiWebApiPlannerError = PiDataProducerPlannerError
PiWebApiAcquisitionError = PiDataProducerAcquisitionError
PiWebApiTimeoutExhaustedError = PiDataProducerTimeoutExhaustedError
PiWebApiMaterializationError = PiDataProducerMaterializationError


class PiWebApiProcessConfigurationError(PiDataProducerError):
    pass
