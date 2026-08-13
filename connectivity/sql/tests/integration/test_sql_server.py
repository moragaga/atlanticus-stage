from __future__ import annotations

import os
import time
import uuid
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from importlib import import_module
from typing import Any

import pytest

from atlanticus.connectivity.sql import (
    SqlClient,
    SqlConnectionError,
    SqlQueryError,
    SqlResultLimitError,
    SqlSettings,
    SqlTimeoutError,
    SqlTimeoutPhase,
)

pytestmark = pytest.mark.integration

_LEGACY_DRIVERS = (
    None,
    'ODBC Driver 17 for SQL Server',
    'ODBC Driver 18 for SQL Server',
)
_SERVER = os.getenv('ATLANTICUS_SQL_SERVER', 'sql-server,1433')
_DATABASE = 'AtlanticusConnectivity'
_SA_PASSWORD = os.getenv('ATLANTICUS_SQL_SA_PASSWORD', 'Atlanticus_Sql_2026!')
_READER_USERNAME = 'atlanticus_reader'
_READER_PASSWORD = os.getenv(
    'ATLANTICUS_SQL_READER_PASSWORD',
    'Atlanticus_Read_2026!',
)


@pytest.fixture(scope='module', autouse=True)
def prepared_sql_server() -> None:
    _require_integration()
    _wait_until_ready()
    _prepare_database()


@pytest.mark.parametrize('legacy_driver', _LEGACY_DRIVERS)
def test_connection_string_compatibility_reads_with_mssql_python(
    legacy_driver: str | None,
) -> None:
    client = SqlClient(
        settings=_reader_settings(
            legacy_driver=legacy_driver,
            query_timeout_seconds=5,
            batch_size=7,
            max_query_rows=5,
        )
    )

    assert client.health_check() is True
    assert client.query('SELECT COUNT_BIG(*) AS total FROM dbo.connectivity_fixture').rows == (
        (25,),
    )


def test_mssql_python_reads_small_and_batched_results() -> None:
    client = SqlClient(
        settings=_reader_settings(
            legacy_driver='ODBC Driver 18 for SQL Server',
            query_timeout_seconds=5,
            batch_size=7,
            max_query_rows=5,
        )
    )

    result = client.query(
        """
        SELECT
            id,
            label,
            amount,
            occurred_at,
            active,
            payload,
            optional_text
        FROM dbo.connectivity_fixture
        WHERE id = ?
        """,
        (3,),
    )

    assert result.columns == (
        'id',
        'label',
        'amount',
        'occurred_at',
        'active',
        'payload',
        'optional_text',
    )
    assert result.rows == (
        (
            3,
            'row-03',
            Decimal('103.0300'),
            datetime(2026, 7, 22, 12, 3),
            True,
            b'payload-03',
            None,
        ),
    )

    empty = client.query(
        'SELECT id, label FROM dbo.connectivity_fixture WHERE id = ?',
        (999,),
    )
    assert empty.columns == ('id', 'label')
    assert empty.rows == ()

    with client.iter_batches(
        'SELECT id, label FROM dbo.connectivity_fixture ORDER BY id'
    ) as stream:
        batches = list(stream)

    assert [batch.row_count for batch in batches] == [7, 7, 7, 4]
    assert [batch.batch_number for batch in batches] == [1, 2, 3, 4]
    assert [batch.row_offset for batch in batches] == [0, 7, 14, 21]
    assert [row[0] for batch in batches for row in batch.rows] == list(range(1, 26))

    with pytest.raises(SqlResultLimitError) as captured:
        client.query('SELECT id FROM dbo.connectivity_fixture ORDER BY id')
    assert captured.value.max_rows == 5


def test_uniqueidentifier_preserves_string_compatibility() -> None:
    client = SqlClient(
        settings=_reader_settings(
            legacy_driver='ODBC Driver 18 for SQL Server',
            query_timeout_seconds=5,
            batch_size=10,
            max_query_rows=10,
        )
    )

    result = client.query('SELECT row_guid FROM dbo.connectivity_fixture WHERE id = ?', (1,))

    assert result.row_count == 1
    assert isinstance(result.rows[0][0], str)
    uuid.UUID(result.rows[0][0])


def test_three_independent_concurrent_reads_are_supported() -> None:
    settings = _reader_settings(
        legacy_driver='ODBC Driver 18 for SQL Server',
        query_timeout_seconds=5,
        batch_size=10,
        max_query_rows=10,
    )

    def read_total() -> tuple[tuple[Any, ...], ...]:
        return (
            SqlClient(settings=settings)
            .query('SELECT COUNT_BIG(*) AS total FROM dbo.connectivity_fixture')
            .rows
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = tuple(executor.map(lambda _: read_total(), range(3)))

    assert results == (((25,),), ((25,),), ((25,),))


def test_mssql_python_failures_are_read_only_sanitized_and_not_retried() -> None:
    client = SqlClient(
        settings=_reader_settings(
            legacy_driver='ODBC Driver 17 for SQL Server',
            query_timeout_seconds=1,
            batch_size=10,
            max_query_rows=10,
        )
    )

    with pytest.raises(SqlTimeoutError) as timeout:
        client.query("WAITFOR DELAY '00:00:03'; SELECT 1 AS value")
    assert timeout.value.phase == SqlTimeoutPhase.QUERY
    assert timeout.value.__cause__ is None

    with pytest.raises(SqlQueryError) as query_error:
        client.query('SELECT private_value FROM dbo.private_missing_table')
    assert 'private' not in repr(query_error.value)
    assert query_error.value.__cause__ is None

    before = client.query('SELECT COUNT_BIG(*) AS total FROM dbo.connectivity_fixture')
    with pytest.raises(SqlQueryError) as write_error:
        client.query(
            'INSERT INTO dbo.connectivity_fixture (id, label, amount, occurred_at, active, payload) '
            "VALUES (999, 'must-not-write', 1, SYSUTCDATETIME(), 1, 0x01); "
            'SELECT 999 AS id'
        )
    after = client.query('SELECT COUNT_BIG(*) AS total FROM dbo.connectivity_fixture')
    assert before.rows == ((25,),)
    assert after.rows == before.rows
    assert 'must-not-write' not in repr(write_error.value)

    invalid = SqlClient(
        settings=SqlSettings(
            connection_string=_connection_string(
                legacy_driver='ODBC Driver 17 for SQL Server',
                database=_DATABASE,
                username=_READER_USERNAME,
                password='private-wrong-password',
            ),
            query_timeout_seconds=2,
        )
    )
    with pytest.raises(SqlConnectionError) as authentication_error:
        invalid.health_check()
    assert 'private' not in repr(authentication_error.value)
    assert _SERVER not in repr(authentication_error.value)


def _reader_settings(
    *,
    legacy_driver: str | None,
    query_timeout_seconds: int,
    batch_size: int,
    max_query_rows: int,
) -> SqlSettings:
    suffix = (
        'MSSQL'
        if legacy_driver is None
        else ('LEGACY_ODBC17' if '17' in legacy_driver else 'LEGACY_ODBC18')
    )
    return SqlSettings.from_mapping(
        values={
            f'SQL_CONNECTION_STRING_{suffix}': _connection_string(
                legacy_driver=legacy_driver,
                database=_DATABASE,
                username=_READER_USERNAME,
                password=_READER_PASSWORD,
            ),
            f'SQL_QUERY_TIMEOUT_SECONDS_{suffix}': query_timeout_seconds,
            f'SQL_BATCH_SIZE_{suffix}': batch_size,
            f'SQL_MAX_QUERY_ROWS_{suffix}': max_query_rows,
        },
        suffix=suffix,
    )


def _connection_string(
    *,
    legacy_driver: str | None,
    database: str,
    username: str,
    password: str,
    connection_timeout_seconds: int | None = 3,
) -> str:
    driver = '' if legacy_driver is None else f'DRIVER={{{legacy_driver}}};'
    connection_timeout = (
        ''
        if connection_timeout_seconds is None
        else f'Connection Timeout={connection_timeout_seconds};'
    )
    return (
        f'{driver}'
        f'SERVER={_SERVER};'
        f'DATABASE={database};'
        f'UID={username};'
        f'PWD={password};'
        'Encrypt=yes;'
        'TrustServerCertificate=yes;'
        f'{connection_timeout}'
    )


def _wait_until_ready() -> None:
    driver = _mssql()
    last_error: Exception | None = None
    connection_string = _connection_string(
        legacy_driver=None,
        database='master',
        username='sa',
        password=_SA_PASSWORD,
        connection_timeout_seconds=None,
    )
    for _ in range(90):
        try:
            connection = driver.connect(
                connection_string,
                autocommit=True,
                timeout=3,
                native_uuid=False,
            )
            connection.close()
            return
        except driver.Error as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError('SQL Server did not become ready') from last_error


def _prepare_database() -> None:
    driver = _mssql()
    master = driver.connect(
        _connection_string(
            legacy_driver=None,
            database='master',
            username='sa',
            password=_SA_PASSWORD,
            connection_timeout_seconds=None,
        ),
        autocommit=True,
        timeout=3,
        native_uuid=False,
    )
    escaped_reader_password = _READER_PASSWORD.replace("'", "''")
    cursor = master.cursor()
    cursor.execute(f"IF DB_ID(N'{_DATABASE}') IS NULL CREATE DATABASE [{_DATABASE}]")
    cursor.execute(
        f"IF SUSER_ID(N'{_READER_USERNAME}') IS NULL "
        f"CREATE LOGIN [{_READER_USERNAME}] WITH PASSWORD = N'{escaped_reader_password}'"
    )
    cursor.close()
    master.close()

    database = driver.connect(
        _connection_string(
            legacy_driver=None,
            database=_DATABASE,
            username='sa',
            password=_SA_PASSWORD,
            connection_timeout_seconds=None,
        ),
        autocommit=True,
        timeout=3,
        native_uuid=False,
    )
    cursor = database.cursor()
    cursor.execute(
        f"IF USER_ID(N'{_READER_USERNAME}') IS NULL "
        f'CREATE USER [{_READER_USERNAME}] FOR LOGIN [{_READER_USERNAME}]'
    )
    cursor.execute(
        f"IF IS_ROLEMEMBER(N'db_datareader', N'{_READER_USERNAME}') <> 1 "
        f'ALTER ROLE [db_datareader] ADD MEMBER [{_READER_USERNAME}]'
    )
    cursor.execute('DROP TABLE IF EXISTS dbo.connectivity_fixture')
    cursor.execute(
        """
        CREATE TABLE dbo.connectivity_fixture (
            id INT NOT NULL PRIMARY KEY,
            row_guid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            label NVARCHAR(50) NOT NULL,
            amount DECIMAL(18, 4) NOT NULL,
            occurred_at DATETIME2(0) NOT NULL,
            active BIT NOT NULL,
            payload VARBINARY(100) NOT NULL,
            optional_text NVARCHAR(50) NULL
        )
        """
    )
    cursor.executemany(
        """
        INSERT INTO dbo.connectivity_fixture (
            id, label, amount, occurred_at, active, payload, optional_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row_id,
                f'row-{row_id:02d}',
                Decimal(f'{100 + row_id}.{row_id:02d}00'),
                datetime(2026, 7, 22, 12, row_id),
                row_id % 2 == 1,
                f'payload-{row_id:02d}'.encode(),
                None if row_id == 3 else f'optional-{row_id:02d}',
            )
            for row_id in range(1, 26)
        ],
    )
    cursor.close()
    database.close()


def _require_integration() -> None:
    if os.getenv('ATLANTICUS_RUN_SQL_INTEGRATION') != '1':
        pytest.skip('SQL integration is disabled. Set ATLANTICUS_RUN_SQL_INTEGRATION=1.')


def _mssql() -> Any:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            'ignore',
            message=r"'return' in a 'finally' block",
            category=SyntaxWarning,
        )
        return import_module('mssql_python')
