# Rutea aplicaciones por source lógico; Fábrica planes y KPIs comparten FABRICA_APPLICATION.
from __future__ import annotations

from dataclasses import dataclass

from ada.processes.kpis_historian.errors import KpiHistorianConfigurationError
from atlanticus.configuration import ConfigurationVariableSpec, ResolvedConfiguration

_DEFAULT_POLL_INTERVAL_SECONDS = 1


@dataclass(frozen=True, slots=True)
class KpiHistorianSettings:
    poll_interval_seconds: int = _DEFAULT_POLL_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        if (
            not isinstance(self.poll_interval_seconds, int)
            or isinstance(self.poll_interval_seconds, bool)
            or self.poll_interval_seconds <= 0
        ):
            raise KpiHistorianConfigurationError(
                'KPI_HISTORIAN_POLL_INTERVAL_SECONDS must be an integer greater than zero'
            )

    @classmethod
    def from_configuration(cls, configuration: ResolvedConfiguration) -> KpiHistorianSettings:
        if not isinstance(configuration, ResolvedConfiguration):
            raise KpiHistorianConfigurationError('configuration must be a ResolvedConfiguration')
        poll_interval_seconds = configuration.get_int('KPI_HISTORIAN_POLL_INTERVAL_SECONDS')
        if poll_interval_seconds is None:
            raise KpiHistorianConfigurationError(
                'KPI_HISTORIAN_POLL_INTERVAL_SECONDS must contain an integer value'
            )
        return cls(poll_interval_seconds=poll_interval_seconds)


def configuration_specs() -> tuple[ConfigurationVariableSpec, ...]:
    return (
        ConfigurationVariableSpec(key='APPLICATION'),
        ConfigurationVariableSpec(key='VOLUMEN_PATH'),
        ConfigurationVariableSpec(
            key='KPI_HISTORIAN_POLL_INTERVAL_SECONDS',
            default=str(_DEFAULT_POLL_INTERVAL_SECONDS),
        ),
        ConfigurationVariableSpec(key='ATLANTICUS_AZURE_OBSERVABILITY_MODE', default='off'),
        ConfigurationVariableSpec(key='ATLANTICUS_AZURE_OBSERVABILITY_PROFILE', required=False),
        ConfigurationVariableSpec(
            key='APPLICATION_INSIGHTS_CONNECTION_STRING',
            required=False,
            sensitive=True,
        ),
    )
