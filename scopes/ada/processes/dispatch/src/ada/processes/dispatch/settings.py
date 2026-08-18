from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ada.processes.dispatch.errors import DispatchProcessConfigurationError
from atlanticus.configuration import ConfigurationVariableSpec, ResolvedConfiguration
from atlanticus.connectivity.sql import SqlSettings, build_sql_configuration_keys

DISPATCH_SQL_SUFFIX = 'DISPATCH'
_DEFAULT_RETRY_ATTEMPTS = 10
_DEFAULT_RETRY_DELAY_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class DispatchSqlRetryPolicy:
    attempts: int = _DEFAULT_RETRY_ATTEMPTS
    delay_seconds: float = _DEFAULT_RETRY_DELAY_SECONDS

    def __post_init__(self) -> None:
        if (
            not isinstance(self.attempts, int)
            or isinstance(self.attempts, bool)
            or self.attempts <= 0
        ):
            raise ValueError('attempts must be an integer greater than zero')
        if isinstance(self.delay_seconds, bool):
            raise ValueError('delay_seconds must be a number greater than or equal to zero')
        try:
            delay_seconds = float(self.delay_seconds)
        except TypeError, ValueError:
            raise ValueError(
                'delay_seconds must be a number greater than or equal to zero'
            ) from None
        if delay_seconds < 0:
            raise ValueError('delay_seconds must be a number greater than or equal to zero')
        object.__setattr__(self, 'delay_seconds', delay_seconds)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> DispatchSqlRetryPolicy:
        return cls(
            attempts=_parse_positive_integer(
                values.get('DISPATCH_SQL_RETRY_ATTEMPTS'),
                default=_DEFAULT_RETRY_ATTEMPTS,
                field_name='DISPATCH_SQL_RETRY_ATTEMPTS',
            ),
            delay_seconds=_parse_non_negative_number(
                values.get('DISPATCH_SQL_RETRY_DELAY_SECONDS'),
                default=_DEFAULT_RETRY_DELAY_SECONDS,
                field_name='DISPATCH_SQL_RETRY_DELAY_SECONDS',
            ),
        )


@dataclass(frozen=True, slots=True)
class DispatchSettings:
    sql: SqlSettings
    retry_policy: DispatchSqlRetryPolicy

    @classmethod
    def from_configuration(cls, configuration: ResolvedConfiguration) -> DispatchSettings:
        if not isinstance(configuration, ResolvedConfiguration):
            raise DispatchProcessConfigurationError('configuration must be a ResolvedConfiguration')
        return cls(
            sql=SqlSettings.from_mapping(
                values=configuration.values,
                suffix=DISPATCH_SQL_SUFFIX,
            ),
            retry_policy=DispatchSqlRetryPolicy.from_mapping(configuration.values),
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


def _parse_positive_integer(value: Any, *, default: int, field_name: str) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise ValueError(f'{field_name} must be an integer greater than zero')
    try:
        parsed = int(value)
    except TypeError, ValueError:
        raise ValueError(f'{field_name} must be an integer greater than zero') from None
    if parsed <= 0 or str(value).strip() not in {str(parsed), f'+{parsed}'}:
        raise ValueError(f'{field_name} must be an integer greater than zero')
    return parsed


def _parse_non_negative_number(value: Any, *, default: float, field_name: str) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise ValueError(f'{field_name} must be a number greater than or equal to zero')
    try:
        parsed = float(value)
    except TypeError, ValueError:
        raise ValueError(f'{field_name} must be a number greater than or equal to zero') from None
    if parsed < 0:
        raise ValueError(f'{field_name} must be a number greater than or equal to zero')
    return parsed
