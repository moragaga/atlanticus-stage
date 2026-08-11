"""Transporte HTTP genérico, síncrono y reutilizable para Atlanticus."""

from pkgutil import extend_path

from atlanticus.connectivity.http.client import HttpClient
from atlanticus.connectivity.http.errors import (
    HttpConfigurationError,
    HttpConnectionError,
    HttpError,
    HttpRequestError,
    HttpResponseError,
    HttpStatusError,
    HttpStreamError,
    HttpTimeoutError,
)
from atlanticus.connectivity.http.models import (
    HttpAuthMode,
    HttpResponse,
    HttpStreamResult,
    HttpTimeoutPhase,
)
from atlanticus.connectivity.http.settings import HttpSettings

__path__ = extend_path(__path__, __name__)

__version__ = '0.1.0'

__all__ = [
    'HttpAuthMode',
    'HttpClient',
    'HttpConfigurationError',
    'HttpConnectionError',
    'HttpError',
    'HttpRequestError',
    'HttpResponse',
    'HttpResponseError',
    'HttpSettings',
    'HttpStatusError',
    'HttpStreamError',
    'HttpStreamResult',
    'HttpTimeoutError',
    'HttpTimeoutPhase',
    '__version__',
]
