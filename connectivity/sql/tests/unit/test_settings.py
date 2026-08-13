from __future__ import annotations

from importlib import import_module

import pytest

from atlanticus.connectivity.sql import (
    DEFAULT_SQL_BATCH_SIZE,
    DEFAULT_SQL_MAX_QUERY_ROWS,
    DEFAULT_SQL_QUERY_TIMEOUT_SECONDS,
    SqlConfigurationError,
    SqlSettings,
    build_sql_configuration_keys,
)

settings_module = import_module('atlanticus.connectivity.sql.settings')


def test_configuration_keys_support_default_and_suffixed_connections() -> None:
    default = build_sql_configuration_keys()
    dispatch = build_sql_configuration_keys(suffix=' dispatch_main ')

    assert default.connection_string == 'SQL_CONNECTION_STRING'
    assert default.query_timeout_seconds == 'SQL_QUERY_TIMEOUT_SECONDS'
    assert dispatch.connection_string == 'SQL_CONNECTION_STRING_DISPATCH_MAIN'
    assert dispatch.batch_size == 'SQL_BATCH_SIZE_DISPATCH_MAIN'
    assert dispatch.max_query_rows == 'SQL_MAX_QUERY_ROWS_DISPATCH_MAIN'


def test_from_mapping_uses_complete_connection_string_and_explicit_limits() -> None:
    connection_string = (
        'DRIVER={ODBC Driver 18 for SQL Server};SERVER=sql.example.test;'
        'DATABASE=dispatch;UID=reader;PWD=private-password;Encrypt=yes;'
    )
    settings = SqlSettings.from_mapping(
        values={
            'SQL_CONNECTION_STRING_DISPATCH': connection_string,
            'SQL_QUERY_TIMEOUT_SECONDS_DISPATCH': '45',
            'SQL_BATCH_SIZE_DISPATCH': '2500',
            'SQL_MAX_QUERY_ROWS_DISPATCH': '500',
        },
        suffix='DISPATCH',
    )

    assert settings.connection_string == connection_string
    assert settings.query_timeout_seconds == 45
    assert settings.batch_size == 2500
    assert settings.max_query_rows == 500
    assert settings.suffix == 'DISPATCH'
    assert 'private-password' not in repr(settings)
    assert 'sql.example.test' not in repr(settings)


def test_mapping_defaults_are_stable() -> None:
    settings = SqlSettings.from_mapping(
        values={'SQL_CONNECTION_STRING': 'DSN=atlanticus-readonly;'}
    )

    assert settings.query_timeout_seconds == DEFAULT_SQL_QUERY_TIMEOUT_SECONDS
    assert settings.batch_size == DEFAULT_SQL_BATCH_SIZE
    assert settings.max_query_rows == DEFAULT_SQL_MAX_QUERY_ROWS


@pytest.mark.parametrize(
    'connection_string',
    (
        '',
        'not-an-odbc-connection-string',
        'DRIVER={ODBC Driver 18 for SQL Server};\nPWD=private;',
        'DSN=atlanticus\x00reader;',
        123,
    ),
)
def test_invalid_connection_strings_are_rejected_without_echoing_values(
    connection_string: object,
) -> None:
    with pytest.raises(SqlConfigurationError) as captured:
        SqlSettings(connection_string=connection_string)  # type: ignore[arg-type]

    assert 'private' not in str(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    ('field_name', 'value'),
    (
        ('query_timeout_seconds', 0),
        ('query_timeout_seconds', '1.5'),
        ('batch_size', -1),
        ('batch_size', True),
        ('max_query_rows', 'many'),
    ),
)
def test_limits_require_positive_integers(field_name: str, value: object) -> None:
    values = {'connection_string': 'DSN=atlanticus;'}
    values[field_name] = value

    with pytest.raises(SqlConfigurationError):
        SqlSettings(**values)  # type: ignore[arg-type]


def test_missing_mapping_reports_only_the_required_key() -> None:
    with pytest.raises(SqlConfigurationError) as captured:
        SqlSettings.from_mapping(
            values={'SQL_QUERY_TIMEOUT_SECONDS_MAIN': '30'},
            suffix='MAIN',
        )

    assert str(captured.value) == 'Missing SQL configuration key: SQL_CONNECTION_STRING_MAIN'


@pytest.mark.parametrize('suffix', ('BAD-SUFFIX', 'DOUBLE__UNDERSCORE', 'SPACE VALUE'))
def test_invalid_suffix_is_rejected(suffix: str) -> None:
    with pytest.raises(SqlConfigurationError):
        build_sql_configuration_keys(suffix=suffix)


@pytest.mark.parametrize(
    'driver',
    (
        'ODBC Driver 17 for SQL Server',
        'ODBC Driver 18 for SQL Server',
        'odbc driver 17 for sql server',
    ),
)
def test_legacy_driver_is_accepted_and_removed_only_for_mssql_runtime(driver: str) -> None:
    connection_string = (
        f'Driver={{{driver}}};'
        'SERVER=sql.example.test;DATABASE=dispatch;'
        'UID=reader;PWD={private;password};Encrypt=yes;'
    )
    settings = SqlSettings(connection_string=connection_string)

    assert settings.connection_string == connection_string
    assert settings_module._prepare_mssql_connection(settings.connection_string) == (
        (
            'SERVER=sql.example.test;DATABASE=dispatch;'
            'UID=reader;PWD={private;password};Encrypt=yes;'
        ),
        None,
    )


def test_connection_string_is_preserved_exactly_in_settings_and_mapping() -> None:
    connection_string = (
        '  SERVER=sql.example.test;DATABASE=dispatch;'
        'UID=reader;PWD={private;password};Encrypt=yes;  '
    )

    direct = SqlSettings(connection_string=connection_string)
    mapped = SqlSettings.from_mapping(values={'SQL_CONNECTION_STRING': connection_string})

    assert direct.connection_string == connection_string
    assert mapped.connection_string == connection_string


def test_connection_string_without_legacy_driver_is_unchanged_for_runtime() -> None:
    connection_string = 'SERVER=sql.example.test;DATABASE=dispatch;UID=reader;PWD=private;'
    settings = SqlSettings(connection_string=connection_string)

    assert settings_module._prepare_mssql_connection(settings.connection_string) == (
        connection_string,
        None,
    )


def test_legacy_connection_timeout_is_removed_and_returned_for_mssql_runtime() -> None:
    connection_string = (
        'Driver={ODBC Driver 17 for SQL Server};SERVER=sql.example.test;'
        'DATABASE=dispatch;UID=reader;PWD=private;Connection Timeout=30;'
    )
    settings = SqlSettings(connection_string=connection_string)

    assert settings.connection_string == connection_string
    assert settings_module._prepare_mssql_connection(settings.connection_string) == (
        'SERVER=sql.example.test;DATABASE=dispatch;UID=reader;PWD=private;',
        30,
    )


@pytest.mark.parametrize('timeout_value', ('-1', '1.5', 'many', ''))
def test_invalid_legacy_connection_timeout_is_rejected(timeout_value: str) -> None:
    connection_string = (
        'SERVER=sql.example.test;DATABASE=dispatch;UID=reader;PWD=private;'
        f'Connection Timeout={timeout_value};'
    )

    with pytest.raises(SqlConfigurationError) as captured:
        SqlSettings(connection_string=connection_string)

    assert 'private' not in str(captured.value)
    assert captured.value.__cause__ is None


def test_duplicate_legacy_connection_timeout_is_rejected() -> None:
    connection_string = (
        'SERVER=sql.example.test;DATABASE=dispatch;UID=reader;PWD=private;'
        'Connection Timeout=3;Connection Timeout=5;'
    )

    with pytest.raises(SqlConfigurationError) as captured:
        SqlSettings(connection_string=connection_string)

    assert 'private' not in str(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    'connection_string',
    (
        'Driver={FreeTDS};SERVER=sql.example.test;',
        'Driver={ODBC Driver 19 for SQL Server};SERVER=sql.example.test;',
        (
            'Driver={ODBC Driver 17 for SQL Server};'
            'Driver={ODBC Driver 18 for SQL Server};SERVER=sql.example.test;'
        ),
        'Driver={ODBC Driver 18 for SQL Server};',
        'Driver={ODBC Driver 18 for SQL Server;SERVER=sql.example.test;',
    ),
)
def test_unsupported_or_ambiguous_legacy_driver_configuration_is_rejected(
    connection_string: str,
) -> None:
    with pytest.raises(SqlConfigurationError) as captured:
        SqlSettings(connection_string=connection_string)

    assert 'sql.example.test' not in str(captured.value)
    assert captured.value.__cause__ is None
