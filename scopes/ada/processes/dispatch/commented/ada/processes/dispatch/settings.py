# Conserva la configuración DISPATCH y delega la política de retry al scope global.
from __future__ import annotations

from dataclasses import dataclass

from ada.processes.dispatch.errors import DispatchProcessConfigurationError
from atlanticus.configuration import ConfigurationVariableSpec, ResolvedConfiguration
from atlanticus.connectivity.sql import SqlSettings, build_sql_configuration_keys
from atlanticus.data_producers.sql import SqlRetryPolicy

DISPATCH_SQL_SUFFIX = 'DISPATCH'
_DEFAULT_RETRY_ATTEMPTS = 10
_DEFAULT_RETRY_DELAY_SECONDS = 5.0


class DispatchSqlRetryPolicy(SqlRetryPolicy):
    @classmethod
    def from_mapping(cls, values):
        resolved = SqlRetryPolicy.from_mapping(values, prefix=DISPATCH_SQL_SUFFIX)
        return cls(attempts=resolved.attempts, delay_seconds=resolved.delay_seconds)


@dataclass(frozen=True, slots=True)
class DispatchSettings:
    sql: SqlSettings
    retry_policy: SqlRetryPolicy

    @classmethod
    def from_configuration(cls, configuration: ResolvedConfiguration) -> DispatchSettings:
        if not isinstance(configuration, ResolvedConfiguration):
            raise DispatchProcessConfigurationError(
                'configuration must be a ResolvedConfiguration'
            )
        return cls(
            sql=SqlSettings.from_mapping(
                values=configuration.values,
                suffix=DISPATCH_SQL_SUFFIX,
            ),
            retry_policy=SqlRetryPolicy.from_mapping(
                configuration.values,
                prefix=DISPATCH_SQL_SUFFIX,
            ),
        )


def configuration_specs() -> tuple[ConfigurationVariableSpec, ...]:
    keys = build_sql_configuration_keys(suffix=DISPATCH_SQL_SUFFIX)
    return (
        ConfigurationVariableSpec(key='APPLICATION'),
        ConfigurationVariableSpec(key='VOLUMEN_PATH'),
        ConfigurationVariableSpec(key=keys.connection_string, sensitive=True),
        ConfigurationVariableSpec(key=keys.query_timeout_seconds, default='200'),
        ConfigurationVariableSpec(key=keys.batch_size, default='10000'),
        ConfigurationVariableSpec(key=keys.max_query_rows, default='500000'),
        ConfigurationVariableSpec(
            key='DISPATCH_SQL_RETRY_ATTEMPTS',
            default=str(_DEFAULT_RETRY_ATTEMPTS),
        ),
        ConfigurationVariableSpec(
            key='DISPATCH_SQL_RETRY_DELAY_SECONDS',
            default=str(_DEFAULT_RETRY_DELAY_SECONDS),
        ),
        ConfigurationVariableSpec(
            key='ATLANTICUS_AZURE_OBSERVABILITY_MODE',
            default='off',
        ),
        ConfigurationVariableSpec(
            key='ATLANTICUS_AZURE_OBSERVABILITY_PROFILE',
            required=False,
        ),
        ConfigurationVariableSpec(
            key='APPLICATION_INSIGHTS_CONNECTION_STRING',
            required=False,
            sensitive=True,
        ),
    )
