"""Conectividad Redis neutral y síncrona para Atlanticus."""

from pkgutil import extend_path

from atlanticus.connectivity.redis.client import RedisClient
from atlanticus.connectivity.redis.errors import (
    RedisAuthenticationError,
    RedisAuthorizationError,
    RedisClosedError,
    RedisConfigurationError,
    RedisConnectionError,
    RedisError,
    RedisOperationError,
    RedisPoolExhaustedError,
    RedisResultLimitError,
)
from atlanticus.connectivity.redis.models import RedisTtl
from atlanticus.connectivity.redis.settings import (
    DEFAULT_REDIS_CONNECTION_TIMEOUT_SECONDS,
    DEFAULT_REDIS_DATABASE,
    DEFAULT_REDIS_MAX_CONNECTIONS,
    DEFAULT_REDIS_MAX_MGET_KEYS,
    DEFAULT_REDIS_OPERATION_TIMEOUT_SECONDS,
    DEFAULT_REDIS_PORT,
    RedisSettings,
)

__path__ = extend_path(__path__, __name__)

# La lista pública explícita protege el boundary del paquete.
__version__ = '0.1.0'

__all__ = [
    'DEFAULT_REDIS_CONNECTION_TIMEOUT_SECONDS',
    'DEFAULT_REDIS_DATABASE',
    'DEFAULT_REDIS_MAX_CONNECTIONS',
    'DEFAULT_REDIS_MAX_MGET_KEYS',
    'DEFAULT_REDIS_OPERATION_TIMEOUT_SECONDS',
    'DEFAULT_REDIS_PORT',
    'RedisAuthenticationError',
    'RedisAuthorizationError',
    'RedisClient',
    'RedisClosedError',
    'RedisConfigurationError',
    'RedisConnectionError',
    'RedisError',
    'RedisOperationError',
    'RedisPoolExhaustedError',
    'RedisResultLimitError',
    'RedisSettings',
    'RedisTtl',
    '__version__',
]
