"""Cliente SQL síncrono de sólo lectura pública y memoria acotada."""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterator, Mapping, Sequence
from importlib import import_module
from time import perf_counter
from typing import Any

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
    SqlSettings,
    _prepare_mssql_connection,
    require_positive_integer,
)
from atlanticus.observability import ErrorInfo, ResultSummary, runtime_guard

_COMPONENT = 'atlanticus.connectivity.sql'
_TIMEOUT_STATES = frozenset({'HYT00', 'HYT01'})
_SQLSTATE_PATTERN = re.compile(r'\b([A-Z0-9]{5})\b')


def _safe_parameters(args: tuple[Any, ...], values: Mapping[str, Any]) -> Mapping[str, Any]:
    instance = args[0] if args else None
    settings = getattr(instance, 'settings', None)
    safe: dict[str, Any] = {}
    if isinstance(settings, SqlSettings) and settings.suffix is not None:
        safe['configuration_suffix'] = settings.suffix
    parameters = values.get('parameters')
    if parameters is not None and not isinstance(parameters, str | bytes | Mapping):
        try:
            safe['parameter_count'] = len(parameters)
        except TypeError:
            pass
    source_tables = values.get('source_tables')
    if source_tables is not None and not isinstance(source_tables, str | bytes | Mapping):
        try:
            safe['source_table_count'] = len(source_tables)
        except TypeError:
            pass
    for name in ('batch_size', 'max_rows'):
        value = values.get(name)
        if value is not None:
            safe[name] = value
    return safe


def _safe_error(error: BaseException) -> ErrorInfo:
    message = str(error) if isinstance(error, SqlError | TypeError) else 'SQL operation failed'
    return ErrorInfo(error_type=type(error).__name__, message=message)


def _query_result(value: Any) -> ResultSummary:
    if not isinstance(value, SqlResult):
        return ResultSummary()
    return ResultSummary(
        attributes={'column_count': len(value.columns)},
        metrics={'row_count': value.row_count},
    )


def _change_marker_result(value: Any) -> ResultSummary:
    if not isinstance(value, tuple) or not all(
        isinstance(item, SqlTableChangeMarker) for item in value
    ):
        return ResultSummary()
    return ResultSummary(metrics={'source_table_count': len(value)})


class SqlBatchStream(Iterator[SqlBatch]):
    """Iterador cerrable que mantiene una sola conexión durante una consulta grande."""

    def __init__(
        self,
        *,
        connection: Any,
        cursor: Any,
        driver_error_type: type[BaseException],
        columns: tuple[str, ...],
        batch_size: int,
    ) -> None:
        self._connection = connection
        self._cursor = cursor
        self._driver_error_type = driver_error_type
        self._columns = columns
        self._batch_size = batch_size
        self._batch_number = 0
        self._row_offset = 0
        self._closed = False

    def __enter__(self) -> SqlBatchStream:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: object | None,
    ) -> None:
        try:
            self.close()
        except SqlConnectionError:
            if exception_type is None:
                raise

    def __iter__(self) -> SqlBatchStream:
        return self

    def __next__(self) -> SqlBatch:
        if self._closed:
            raise StopIteration
        started_at = perf_counter()
        try:
            fetched = self._cursor.fetchmany(self._batch_size)
        except self._driver_error_type as error:
            self._close_without_error()
            raise _database_error(error, phase=SqlTimeoutPhase.QUERY) from None
        except Exception:
            self._close_without_error()
            raise SqlQueryError('SQL batch read failed') from None
        if not fetched:
            self.close()
            raise StopIteration
        rows = tuple(tuple(row) for row in fetched)
        self._batch_number += 1
        batch = SqlBatch(
            columns=self._columns,
            rows=rows,
            batch_number=self._batch_number,
            row_offset=self._row_offset,
            duration_ms=round((perf_counter() - started_at) * 1000, 4),
        )
        self._row_offset += batch.row_count
        return batch

    def close(self) -> None:
        """Cierra cursor y conexión de forma idempotente."""

        if self._closed:
            return
        self._closed = True
        cursor_error = _close_resource(self._cursor)
        connection_error = _close_resource(self._connection)
        if cursor_error or connection_error:
            raise SqlConnectionError('Could not close SQL batch stream') from None

    def _close_without_error(self) -> None:
        if self._closed:
            return
        self._closed = True
        _close_resource(self._cursor)
        _close_resource(self._connection)

    def __del__(self) -> None:
        try:
            self._close_without_error()
        except Exception:
            pass


class SqlClient:
    """Ejecuta consultas parametrizadas sin conocer tablas ni reglas de negocio."""

    def __init__(self, *, settings: SqlSettings) -> None:
        if not isinstance(settings, SqlSettings):
            raise SqlConfigurationError('settings must be SqlSettings')
        self.settings = settings

    @runtime_guard(
        operation='sql.health_check',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def health_check(self) -> bool:
        """Comprueba conexión y una consulta mínima mediante un único intento."""

        result = self._query_impl(
            statement='SELECT CAST(1 AS INTEGER) AS health',
            parameters=(),
            max_rows=1,
        )
        return result.rows == ((1,),)

    @runtime_guard(
        operation='sql.query',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_query_result,
        error_mapper=_safe_error,
    )
    def query(
        self,
        statement: str,
        parameters: Sequence[Any] | None = None,
        *,
        max_rows: int | None = None,
    ) -> SqlResult:
        """Materializa un resultado pequeño con un límite estricto de filas."""

        normalized_max_rows = (
            self.settings.max_query_rows
            if max_rows is None
            else require_positive_integer(max_rows, 'max_rows')
        )
        return self._query_impl(
            statement=statement,
            parameters=_normalize_parameters(parameters),
            max_rows=normalized_max_rows,
        )

    @runtime_guard(
        operation='sql.table_change_markers',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_change_marker_result,
        error_mapper=_safe_error,
    )
    def table_change_markers(
        self,
        source_tables: Sequence[str],
    ) -> tuple[SqlTableChangeMarker, ...]:
        """Lee marcas DML de varias tablas mediante una sola consulta de sistema."""

        normalized_tables = _normalize_source_tables(source_tables)
        statement, parameters = _build_table_change_statement(normalized_tables)
        result = self._query_impl(
            statement=statement,
            parameters=parameters,
            max_rows=len(normalized_tables),
        )
        expected_columns = (
            'source_table',
            'generation_token',
            'last_user_update_token',
            'user_updates',
            'auto_close_enabled',
        )
        if result.columns != expected_columns or result.row_count != len(normalized_tables):
            raise SqlQueryContractError('SQL table change marker result is incomplete')
        if any(bool(row[4]) for row in result.rows):
            raise SqlQueryContractError(
                'SQL table change detection is unsafe when AUTO_CLOSE is enabled'
            )
        markers = tuple(
            SqlTableChangeMarker(
                source_table=str(row[0]),
                generation_token=str(row[1]),
                last_user_update_token=None if row[2] is None else str(row[2]),
                user_updates=int(row[3]),
            )
            for row in result.rows
        )
        if {item.source_table.lower() for item in markers} != {
            item.lower() for item in normalized_tables
        }:
            raise SqlQueryContractError('SQL table change marker result does not match request')
        return markers

    @runtime_guard(
        operation='sql.iter_batches',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def iter_batches(
        self,
        statement: str,
        parameters: Sequence[Any] | None = None,
        *,
        batch_size: int | None = None,
    ) -> SqlBatchStream:
        """Abre una lectura por ``fetchmany()`` y entrega un stream cerrable."""

        normalized_statement = _normalize_statement(statement)
        normalized_parameters = _normalize_parameters(parameters)
        normalized_batch_size = (
            self.settings.batch_size
            if batch_size is None
            else require_positive_integer(batch_size, 'batch_size')
        )
        connection, driver_error_type = self._connect()
        cursor: Any | None = None
        try:
            cursor = connection.cursor()
            _execute(cursor, normalized_statement, normalized_parameters)
            columns = _read_columns(cursor)
        except SqlError:
            if cursor is not None:
                _close_resource(cursor)
            _close_resource(connection)
            raise
        except driver_error_type as error:
            if cursor is not None:
                _close_resource(cursor)
            _close_resource(connection)
            raise _database_error(error, phase=SqlTimeoutPhase.QUERY) from None
        except Exception:
            if cursor is not None:
                _close_resource(cursor)
            _close_resource(connection)
            raise SqlQueryError('SQL query failed') from None
        return SqlBatchStream(
            connection=connection,
            cursor=cursor,
            driver_error_type=driver_error_type,
            columns=columns,
            batch_size=normalized_batch_size,
        )

    def _query_impl(
        self,
        *,
        statement: str,
        parameters: Sequence[Any],
        max_rows: int,
    ) -> SqlResult:
        normalized_statement = _normalize_statement(statement)
        normalized_parameters = _normalize_parameters(parameters)
        started_at = perf_counter()
        connection, driver_error_type = self._connect()
        cursor: Any | None = None
        result: SqlResult | None = None
        operation_error: BaseException | None = None
        try:
            cursor = connection.cursor()
            _execute(cursor, normalized_statement, normalized_parameters)
            columns = _read_columns(cursor)
            fetched = cursor.fetchmany(max_rows + 1)
            if len(fetched) > max_rows:
                raise SqlResultLimitError(max_rows=max_rows)
            result = SqlResult(
                columns=columns,
                rows=tuple(tuple(row) for row in fetched),
                duration_ms=round((perf_counter() - started_at) * 1000, 4),
            )
        except SqlError as error:
            operation_error = error
        except driver_error_type as error:
            operation_error = _database_error(error, phase=SqlTimeoutPhase.QUERY)
        except Exception:
            operation_error = SqlQueryError('SQL query failed')
        cursor_error = _close_resource(cursor)
        connection_error = _close_resource(connection)
        if operation_error is not None:
            raise operation_error from None
        if cursor_error or connection_error:
            raise SqlConnectionError('Could not close SQL connection') from None
        if result is None:
            raise SqlQueryError('SQL query failed')
        return result

    def _connect(self) -> tuple[Any, type[BaseException]]:
        driver = _load_mssql_driver()
        connection: Any | None = None
        try:
            connection_string, connection_timeout_seconds = _prepare_mssql_connection(
                self.settings.connection_string
            )
            connection = driver.connect(
                connection_string,
                autocommit=True,
                timeout=0 if connection_timeout_seconds is None else connection_timeout_seconds,
                native_uuid=False,
            )
            connection.timeout = self.settings.query_timeout_seconds
            return connection, driver.Error
        except driver.Error as error:
            _close_resource(connection)
            raise _database_error(error, phase=SqlTimeoutPhase.CONNECT) from None
        except Exception:
            _close_resource(connection)
            raise SqlConnectionError('Could not open SQL connection') from None


def _normalize_source_tables(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, str | bytes | bytearray | memoryview | Mapping):
        raise SqlQueryContractError('source_tables must be a sequence of schema.table names')
    try:
        normalized = tuple(_normalize_source_table(value) for value in values)
    except TypeError:
        raise SqlQueryContractError(
            'source_tables must be a sequence of schema.table names'
        ) from None
    if not normalized:
        raise SqlQueryContractError('source_tables must not be empty')
    if len({item.lower() for item in normalized}) != len(normalized):
        raise SqlQueryContractError('source_tables must not contain duplicates')
    return normalized


def _normalize_source_table(value: str) -> str:
    if not isinstance(value, str):
        raise SqlQueryContractError('source table names must be strings')
    parts = tuple(part.strip() for part in value.split('.'))
    if len(parts) != 2 or any(not part for part in parts):
        raise SqlQueryContractError('source table names must use schema.table format')
    if any(re.fullmatch(r'[A-Za-z0-9_]+', part) is None for part in parts):
        raise SqlQueryContractError('source table names contain unsupported characters')
    return '.'.join(parts)


def _build_table_change_statement(
    source_tables: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    requested_rows = ', '.join('(?, ?)' for _ in source_tables)
    parameters = tuple(part for table in source_tables for part in table.split('.', 1))
    statement = f"""WITH requested(schema_name, table_name) AS (
    SELECT schema_name, table_name
    FROM (VALUES {requested_rows}) AS requested_values(schema_name, table_name)
),
usage_stats AS (
    SELECT
        object_id,
        MAX(last_user_update) AS last_user_update,
        SUM(CONVERT(bigint, user_updates)) AS user_updates
    FROM sys.dm_db_index_usage_stats
    WHERE database_id = DB_ID()
    GROUP BY object_id
)
SELECT
    requested.schema_name + '.' + requested.table_name AS source_table,
    CONCAT(
        CONVERT(varchar(33), server_generation.create_date, 126),
        '|', database_info.database_id,
        '|', CONVERT(varchar(33), database_info.create_date, 126)
    ) AS generation_token,
    CONVERT(varchar(33), usage_stats.last_user_update, 126) AS last_user_update_token,
    COALESCE(usage_stats.user_updates, 0) AS user_updates,
    CONVERT(int, database_info.is_auto_close_on) AS auto_close_enabled
FROM requested
JOIN sys.schemas AS schemas
    ON schemas.name = requested.schema_name
JOIN sys.tables AS tables
    ON tables.schema_id = schemas.schema_id
    AND tables.name = requested.table_name
JOIN sys.databases AS database_info
    ON database_info.database_id = DB_ID()
CROSS JOIN (
    SELECT create_date
    FROM sys.databases
    WHERE name = 'tempdb'
) AS server_generation
LEFT JOIN usage_stats
    ON usage_stats.object_id = tables.object_id
ORDER BY requested.schema_name, requested.table_name"""
    return statement, parameters


def _normalize_statement(value: str) -> str:
    if not isinstance(value, str):
        raise SqlQueryContractError('statement must be a string')
    normalized = value.strip()
    if not normalized:
        raise SqlQueryContractError('statement must not be empty')
    if '\x00' in normalized:
        raise SqlQueryContractError('statement must not contain null characters')
    return normalized


def _normalize_parameters(value: Sequence[Any] | None) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, str | bytes | bytearray | memoryview | Mapping):
        raise SqlQueryContractError('parameters must be a positional sequence')
    try:
        return tuple(value)
    except TypeError:
        raise SqlQueryContractError('parameters must be a positional sequence') from None


def _execute(cursor: Any, statement: str, parameters: Sequence[Any]) -> None:
    if parameters:
        cursor.execute(statement, *parameters)
    else:
        cursor.execute(statement)


def _read_columns(cursor: Any) -> tuple[str, ...]:
    description = cursor.description
    if not description:
        raise SqlQueryContractError('SQL statement did not produce a result set')
    columns = tuple(str(column[0]) for column in description)
    if any(not column.strip() for column in columns):
        raise SqlQueryError('SQL result contains an unnamed column')
    return columns


def _database_error(error: BaseException, *, phase: SqlTimeoutPhase) -> SqlError:
    if _is_timeout(error):
        return SqlTimeoutError(phase=phase)
    if phase is SqlTimeoutPhase.CONNECT:
        return SqlConnectionError('Could not open SQL connection')
    return SqlQueryError('SQL query failed')


def _is_timeout(error: BaseException) -> bool:
    values = [*error.args]
    values.extend(getattr(error, name, None) for name in ('driver_error', 'ddbc_error', 'message'))
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip().upper()
        if normalized in _TIMEOUT_STATES:
            return True
        match = _SQLSTATE_PATTERN.search(normalized)
        if match is not None and match.group(1) in _TIMEOUT_STATES:
            return True
        if 'TIMEOUT' in normalized or 'TIMED OUT' in normalized:
            return True
    return False


def _close_resource(resource: Any | None) -> bool:
    if resource is None:
        return False
    close = getattr(resource, 'close', None)
    if not callable(close):
        return False
    try:
        close()
    except Exception:
        return True
    return False


def _load_mssql_driver() -> Any:
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                'ignore',
                message=r"'return' in a 'finally' block",
                category=SyntaxWarning,
            )
            return import_module('mssql_python')
    except ImportError, OSError:
        raise SqlConnectionError('SQL driver runtime is unavailable') from None
