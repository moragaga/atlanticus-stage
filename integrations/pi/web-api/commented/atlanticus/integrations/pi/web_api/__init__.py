# API pública del paquete: exporta solo los contratos necesarios para consumidores.
# Los recursos internos quedan fuera de __all__ para no ampliar el contrato accidentalmente.

from pkgutil import extend_path

from atlanticus.integrations.pi.web_api.client import PiWebApiClient
from atlanticus.integrations.pi.web_api.errors import (
    PiWebApiConfigurationError,
    PiWebApiConnectionError,
    PiWebApiError,
    PiWebApiRequestError,
    PiWebApiResponseError,
    PiWebApiStatusError,
    PiWebApiTimeoutError,
)
from atlanticus.integrations.pi.web_api.models import PiPointWebIdResult
from atlanticus.integrations.pi.web_api.settings import PiWebApiLimits, PiWebApiSettings

__path__ = extend_path(__path__, __name__)

__version__ = '0.1.0'

__all__ = [
    'PiPointWebIdResult',
    'PiWebApiClient',
    'PiWebApiConfigurationError',
    'PiWebApiConnectionError',
    'PiWebApiError',
    'PiWebApiLimits',
    'PiWebApiRequestError',
    'PiWebApiResponseError',
    'PiWebApiSettings',
    'PiWebApiStatusError',
    'PiWebApiTimeoutError',
]
