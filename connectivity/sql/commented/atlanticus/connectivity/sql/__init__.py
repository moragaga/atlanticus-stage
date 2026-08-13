# Espejo comentado del contrato público del conector SQL.
# Mantiene exactamente el mismo código ejecutable que producción.
"""Conectividad SQL neutral, síncrona y orientada a lectura para Atlanticus."""

from pkgutil import extend_path

from atlanticus.connectivity.sql.client import SqlBatchStream, SqlClient
from atlanticus.connectivity.sql.errors import (
    SqlConfigurationError,
    SqlConnectionError,
    SqlError,
    SqlQueryContractError,
    SqlQueryError,
    SqlResultLimitError,
    SqlTimeoutError,
)
from atlanticus.connectivity.sql.models import (
    SqlBatch,
    SqlResult,
    SqlTableChangeMarker,
    SqlTimeoutPhase,
)
from atlanticus.connectivity.sql.settings import (
    DEFAULT_SQL_BATCH_SIZE,
    DEFAULT_SQL_MAX_QUERY_ROWS,
    DEFAULT_SQL_QUERY_TIMEOUT_SECONDS,
    SqlConfigurationKeys,
    SqlSettings,
    build_sql_configuration_keys,
    normalize_configuration_suffix,
)

__path__ = extend_path(__path__, __name__)

__version__ = '0.1.0'

__all__ = [
    'DEFAULT_SQL_BATCH_SIZE',
    'DEFAULT_SQL_MAX_QUERY_ROWS',
    'DEFAULT_SQL_QUERY_TIMEOUT_SECONDS',
    'SqlBatch',
    'SqlBatchStream',
    'SqlClient',
    'SqlConfigurationError',
    'SqlConfigurationKeys',
    'SqlConnectionError',
    'SqlError',
    'SqlQueryContractError',
    'SqlQueryError',
    'SqlResult',
    'SqlResultLimitError',
    'SqlSettings',
    'SqlTableChangeMarker',
    'SqlTimeoutError',
    'SqlTimeoutPhase',
    '__version__',
    'build_sql_configuration_keys',
    'normalize_configuration_suffix',
]
