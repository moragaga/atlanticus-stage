from __future__ import annotations

from dataclasses import dataclass

from ada.processes.blockgrade.errors import BlockgradeProcessConfigurationError
from atlanticus.configuration import ConfigurationVariableSpec, ResolvedConfiguration
from atlanticus.connectivity.sql import SqlSettings, build_sql_configuration_keys
from atlanticus.data_producers.sql import SqlRetryPolicy

BLOCKGRADE_SQL_SUFFIX = 'BLOCKGRADE'
_DEFAULT_RETRY_ATTEMPTS = 10
_DEFAULT_RETRY_DELAY_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class BlockgradeSettings:
    sql: SqlSettings
    retry_policy: SqlRetryPolicy

    @classmethod
    def from_configuration(cls, configuration: ResolvedConfiguration) -> BlockgradeSettings:
        if not isinstance(configuration, ResolvedConfiguration):
            raise BlockgradeProcessConfigurationError(
                'configuration must be a ResolvedConfiguration'
            )
        return cls(
            sql=SqlSettings.from_mapping(
                values=configuration.values,
                suffix=BLOCKGRADE_SQL_SUFFIX,
            ),
            retry_policy=SqlRetryPolicy.from_mapping(
                configuration.values,
                prefix=BLOCKGRADE_SQL_SUFFIX,
            ),
        )


def configuration_specs() -> tuple[ConfigurationVariableSpec, ...]:
    keys = build_sql_configuration_keys(suffix=BLOCKGRADE_SQL_SUFFIX)
    return (
        ConfigurationVariableSpec(key='APPLICATION'),
        ConfigurationVariableSpec(key='VOLUMEN_PATH'),
        ConfigurationVariableSpec(key=keys.connection_string, sensitive=True),
        ConfigurationVariableSpec(key=keys.query_timeout_seconds, default='200'),
        ConfigurationVariableSpec(key=keys.batch_size, default='10000'),
        ConfigurationVariableSpec(key=keys.max_query_rows, default='500000'),
        ConfigurationVariableSpec(
            key='BLOCKGRADE_SQL_RETRY_ATTEMPTS',
            default=str(_DEFAULT_RETRY_ATTEMPTS),
        ),
        ConfigurationVariableSpec(
            key='BLOCKGRADE_SQL_RETRY_DELAY_SECONDS',
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
