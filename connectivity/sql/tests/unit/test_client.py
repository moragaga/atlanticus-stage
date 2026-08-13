from __future__ import annotations

import warnings
from collections.abc import Sequence
from typing import Any

import pytest

import atlanticus.connectivity.sql.client as client_module
from atlanticus.connectivity.sql import (
    SqlClient,
    SqlConnectionError,
    SqlQueryContractError,
    SqlQueryError,
    SqlResultLimitError,
    SqlSettings,
    SqlTimeoutError,
    SqlTimeoutPhase,
)

_CONNECTION_STRING = (
    'DRIVER={ODBC Driver 18 for SQL Server};SERVER=private-server;'
    'UID=private-user;PWD=private-password;'
)


class FakeDriverError(Exception):
    pass


class FakeOperationalError(FakeDriverError):
    pass


class FakeProgrammingError(FakeDriverError):
    pass


class FakeCursor:
    def __init__(
        self,
        *,
        columns: tuple[str, ...] | None = ('id',),
        rows: Sequence[Sequence[Any]] = (),
        execute_error: Exception | None = None,
        fetch_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.description = (
            None
            if columns is None
            else tuple((column, None, None, None, None, None, None) for column in columns)
        )
        self.rows = [tuple(row) for row in rows]
        self.execute_error = execute_error
        self.fetch_error = fetch_error
        self.close_error = close_error
        self.executions: list[tuple[Any, ...]] = []
        self.fetch_sizes: list[int] = []
        self.closed = False

    def execute(self, *values: Any) -> FakeCursor:
        self.executions.append(values)
        if self.execute_error is not None:
            raise self.execute_error
        return self

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        self.fetch_sizes.append(size)
        if self.fetch_error is not None:
            raise self.fetch_error
        fetched = self.rows[:size]
        self.rows = self.rows[size:]
        return fetched

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class FakeConnection:
    def __init__(
        self,
        *,
        cursor: FakeCursor,
        close_error: Exception | None = None,
    ) -> None:
        self.cursor_value = cursor
        self.close_error = close_error
        self.timeout = 0
        self.closed = False
        self.cursor_calls = 0

    def cursor(self) -> FakeCursor:
        self.cursor_calls += 1
        return self.cursor_value

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


def _settings(**values: Any) -> SqlSettings:
    return SqlSettings(connection_string=_CONNECTION_STRING, **values)


def _install_connection(
    monkeypatch: pytest.MonkeyPatch,
    connection: FakeConnection,
) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class FakeDriver:
        Error = FakeDriverError

        @staticmethod
        def connect(*args: Any, **kwargs: Any) -> FakeConnection:
            calls.append((args, kwargs))
            return connection

    monkeypatch.setattr(client_module, '_load_mssql_driver', lambda: FakeDriver)
    return calls


def test_query_uses_positional_parameters_and_a_single_bounded_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(columns=('id', 'name'), rows=((1, 'one'), (2, None)))
    connection = FakeConnection(cursor=cursor)
    calls = _install_connection(monkeypatch, connection)
    client = SqlClient(settings=_settings(query_timeout_seconds=12, max_query_rows=5))

    result = client.query(
        'SELECT id, name FROM private_table WHERE id >= ?',
        (1,),
    )

    assert result.columns == ('id', 'name')
    assert result.rows == ((1, 'one'), (2, None))
    assert result.row_count == 2
    assert len(calls) == 1
    assert calls[0] == (
        ('SERVER=private-server;UID=private-user;PWD=private-password;',),
        {'autocommit': True, 'timeout': 0, 'native_uuid': False},
    )
    assert connection.timeout == 12
    assert cursor.executions == [('SELECT id, name FROM private_table WHERE id >= ?', 1)]
    assert cursor.fetch_sizes == [6]
    assert cursor.closed is True
    assert connection.closed is True


def test_legacy_connection_timeout_is_forwarded_to_mssql_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(rows=((1,),))
    connection = FakeConnection(cursor=cursor)
    calls = _install_connection(monkeypatch, connection)
    client = SqlClient(
        settings=SqlSettings(
            connection_string=(
                'DRIVER={ODBC Driver 17 for SQL Server};SERVER=private-server;'
                'UID=private-user;PWD=private-password;Connection Timeout=9;'
            )
        )
    )

    assert client.query('SELECT 1').rows == ((1,),)
    assert calls == [
        (
            ('SERVER=private-server;UID=private-user;PWD=private-password;',),
            {'autocommit': True, 'timeout': 9, 'native_uuid': False},
        )
    ]


def test_query_limit_is_strict_and_closes_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(rows=((1,), (2,), (3,)))
    connection = FakeConnection(cursor=cursor)
    _install_connection(monkeypatch, connection)
    client = SqlClient(settings=_settings(max_query_rows=2))

    with pytest.raises(SqlResultLimitError) as captured:
        client.query('SELECT id FROM private_table')

    assert captured.value.max_rows == 2
    assert cursor.fetch_sizes == [3]
    assert cursor.closed is True
    assert connection.closed is True
    assert 'private_table' not in repr(captured.value)


def test_iter_batches_keeps_one_connection_and_closes_on_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(columns=('id',), rows=((1,), (2,), (3,), (4,), (5,)))
    connection = FakeConnection(cursor=cursor)
    calls = _install_connection(monkeypatch, connection)
    client = SqlClient(settings=_settings(batch_size=2))

    with client.iter_batches('SELECT id FROM private_table ORDER BY id') as stream:
        batches = list(stream)

    assert len(calls) == 1
    assert [batch.rows for batch in batches] == [((1,), (2,)), ((3,), (4,)), ((5,),)]
    assert [batch.batch_number for batch in batches] == [1, 2, 3]
    assert [batch.row_offset for batch in batches] == [0, 2, 4]
    assert cursor.fetch_sizes == [2, 2, 2, 2]
    assert cursor.closed is True
    assert connection.closed is True


def test_batch_stream_context_closes_after_an_early_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(rows=((1,), (2,), (3,)))
    connection = FakeConnection(cursor=cursor)
    _install_connection(monkeypatch, connection)
    client = SqlClient(settings=_settings(batch_size=1))

    with client.iter_batches('SELECT id FROM private_table') as stream:
        assert next(stream).rows == ((1,),)

    assert cursor.closed is True
    assert connection.closed is True
    with pytest.raises(StopIteration):
        next(stream)


def test_batch_stream_preserves_body_error_when_close_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(rows=((1,),), close_error=RuntimeError('private-cursor-close'))
    connection = FakeConnection(
        cursor=cursor,
        close_error=RuntimeError('private-connection-close'),
    )
    _install_connection(monkeypatch, connection)
    client = SqlClient(settings=_settings())

    with pytest.raises(ValueError, match='primary failure'):
        with client.iter_batches('SELECT id FROM private_table'):
            raise ValueError('primary failure')

    assert cursor.closed is True
    assert connection.closed is True


def test_statement_without_a_result_set_is_rejected_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(columns=None)
    connection = FakeConnection(cursor=cursor)
    _install_connection(monkeypatch, connection)
    client = SqlClient(settings=_settings())

    with pytest.raises(SqlQueryContractError):
        client.query('UPDATE private_table SET value = 1')

    assert cursor.closed is True
    assert connection.closed is True


@pytest.mark.parametrize(
    ('statement', 'parameters'),
    (
        ('', ()),
        ('SELECT 1\x00', ()),
        (123, ()),
        ('SELECT 1', 'not-positional'),
        ('SELECT 1', {'named': 'not-supported'}),
    ),
)
def test_invalid_contract_is_rejected_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
    statement: Any,
    parameters: Any,
) -> None:
    calls = 0

    class FakeDriver:
        Error = FakeDriverError

        @staticmethod
        def connect(*args: Any, **kwargs: Any) -> None:
            nonlocal calls
            calls += 1

    monkeypatch.setattr(client_module, '_load_mssql_driver', lambda: FakeDriver)
    client = SqlClient(settings=_settings())

    with pytest.raises(SqlQueryContractError):
        client.query(statement, parameters)

    assert calls == 0


def test_connection_timeout_is_sanitized_and_never_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class FakeDriver:
        Error = FakeDriverError

        @staticmethod
        def connect(*args: Any, **kwargs: Any) -> None:
            nonlocal calls
            calls += 1
            raise FakeOperationalError('HYT00', 'private-server private-password')

    monkeypatch.setattr(client_module, '_load_mssql_driver', lambda: FakeDriver)
    client = SqlClient(settings=_settings())

    with pytest.raises(SqlTimeoutError) as captured:
        client.query('SELECT private_column FROM private_table')

    assert calls == 1
    assert captured.value.phase == SqlTimeoutPhase.CONNECT
    assert captured.value.__cause__ is None
    assert 'private' not in repr(captured.value)


def test_missing_sql_driver_runtime_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_driver(name: str) -> None:
        raise OSError(f'private-native-path/{name}')

    monkeypatch.setattr(client_module, 'import_module', missing_driver)
    client = SqlClient(settings=_settings())

    with pytest.raises(SqlConnectionError) as captured:
        client.health_check()

    assert str(captured.value) == 'SQL driver runtime is unavailable'
    assert captured.value.__cause__ is None
    assert 'private' not in repr(captured.value)


def test_known_mssql_import_warning_is_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()

    def import_driver(name: str) -> object:
        assert name == 'mssql_python'
        warnings.warn(
            "'return' in a 'finally' block",
            SyntaxWarning,
            stacklevel=1,
        )
        return sentinel

    monkeypatch.setattr(client_module, 'import_module', import_driver)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        assert client_module._load_mssql_driver() is sentinel

    assert captured == []


def test_unrelated_syntax_warning_is_not_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()

    def import_driver(name: str) -> object:
        assert name == 'mssql_python'
        warnings.warn('different dependency syntax warning', SyntaxWarning, stacklevel=1)
        return sentinel

    monkeypatch.setattr(client_module, 'import_module', import_driver)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        assert client_module._load_mssql_driver() is sentinel

    assert len(captured) == 1
    assert str(captured[0].message) == 'different dependency syntax warning'


def test_query_timeout_is_sanitized_and_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(
        execute_error=FakeOperationalError(
            'HYT01',
            'private-server SELECT private FROM private_table',
        )
    )
    connection = FakeConnection(cursor=cursor)
    calls = _install_connection(monkeypatch, connection)
    client = SqlClient(settings=_settings())

    with pytest.raises(SqlTimeoutError) as captured:
        client.query('SELECT private FROM private_table')

    assert len(calls) == 1
    assert len(cursor.executions) == 1
    assert captured.value.phase == SqlTimeoutPhase.QUERY
    assert captured.value.__cause__ is None
    assert 'private' not in repr(captured.value)
    assert cursor.closed is True
    assert connection.closed is True


def test_batch_fetch_error_is_sanitized_and_closes_the_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(
        fetch_error=FakeProgrammingError(
            '42000',
            'private-server SELECT private FROM private_table',
        )
    )
    connection = FakeConnection(cursor=cursor)
    _install_connection(monkeypatch, connection)
    client = SqlClient(settings=_settings())

    stream = client.iter_batches('SELECT private FROM private_table')
    with pytest.raises(SqlQueryError) as captured:
        next(stream)

    assert 'private' not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert cursor.closed is True
    assert connection.closed is True


def test_health_check_uses_the_same_single_attempt_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(columns=('health',), rows=((1,),))
    connection = FakeConnection(cursor=cursor)
    calls = _install_connection(monkeypatch, connection)

    assert SqlClient(settings=_settings()).health_check() is True
    assert len(calls) == 1
    assert cursor.executions == [('SELECT CAST(1 AS INTEGER) AS health',)]


def test_close_failure_is_normalized_without_driver_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(rows=((1,),), close_error=RuntimeError('private-cursor'))
    connection = FakeConnection(cursor=cursor)
    _install_connection(monkeypatch, connection)

    with pytest.raises(SqlConnectionError) as captured:
        SqlClient(settings=_settings()).query('SELECT id FROM private_table')

    assert 'private' not in repr(captured.value)


def test_table_change_markers_use_one_system_query_for_all_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(
        columns=(
            'source_table',
            'generation_token',
            'last_user_update_token',
            'user_updates',
            'auto_close_enabled',
        ),
        rows=(
            (
                'dbo.tiempos_mlp',
                '2026-08-10T10:00:00|7|2025-01-01T00:00:00',
                '2026-08-10T19:20:00',
                21,
                0,
            ),
            (
                'std.StdShiftDumps',
                '2026-08-10T10:00:00|7|2025-01-01T00:00:00',
                '2026-08-10T19:21:00',
                84,
                0,
            ),
        ),
    )
    connection = FakeConnection(cursor=cursor)
    _install_connection(monkeypatch, connection)
    client = SqlClient(settings=_settings())

    markers = client.table_change_markers(('dbo.tiempos_mlp', 'std.StdShiftDumps'))

    assert [item.source_table for item in markers] == ['dbo.tiempos_mlp', 'std.StdShiftDumps']
    assert markers[0].generation_token == '2026-08-10T10:00:00|7|2025-01-01T00:00:00'
    assert markers[1].user_updates == 84
    assert len(cursor.executions) == 1
    assert 'sys.dm_db_index_usage_stats' in cursor.executions[0][0]
    assert 'sys.databases' in cursor.executions[0][0]
    assert cursor.executions[0][1:] == ('dbo', 'tiempos_mlp', 'std', 'StdShiftDumps')
    assert cursor.fetch_sizes == [3]


def test_table_change_markers_reject_invalid_or_duplicate_sources() -> None:
    client = SqlClient(settings=_settings())

    with pytest.raises(SqlQueryContractError):
        client.table_change_markers(('missing_schema',))
    with pytest.raises(SqlQueryContractError):
        client.table_change_markers(('std.Table', 'STD.table'))


def test_table_change_markers_reject_auto_close_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(
        columns=(
            'source_table',
            'generation_token',
            'last_user_update_token',
            'user_updates',
            'auto_close_enabled',
        ),
        rows=(('std.StdShiftDumps', 'generation-1', None, 0, 1),),
    )
    connection = FakeConnection(cursor=cursor)
    _install_connection(monkeypatch, connection)

    with pytest.raises(SqlQueryContractError, match='AUTO_CLOSE'):
        SqlClient(settings=_settings()).table_change_markers(('std.StdShiftDumps',))
