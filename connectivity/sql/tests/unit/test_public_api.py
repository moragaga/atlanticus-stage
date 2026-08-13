from __future__ import annotations

import atlanticus.connectivity.sql as sql


def test_public_api_and_version_are_stable() -> None:
    assert sql.__version__ == '0.1.0'
    assert sql.__all__ == [
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
