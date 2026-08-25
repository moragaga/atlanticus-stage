from __future__ import annotations

from dataclasses import dataclass

from ada.data.core import DataSource
from ada.data.sources import DataSourceApplications, DataSourceRoutingError, PiSourceProvider
from ada.kpis.core import KpiCatalog
from ada.processes.kpis.errors import KpiProcessConfigurationError
from atlanticus.configuration import ConfigurationVariableSpec, ResolvedConfiguration

_PI_SOURCE_VALUES = {
    'PI_WEB_API': PiSourceProvider.PI_WEB_API,
    'NOTPII': PiSourceProvider.NOTPII,
}
_DEFAULT_POLL_INTERVAL_SECONDS = 1


@dataclass(frozen=True, slots=True)
class KpiProcessSettings:
    pi_source: PiSourceProvider
    source_applications: DataSourceApplications
    poll_interval_seconds: int = _DEFAULT_POLL_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.pi_source, PiSourceProvider):
            raise KpiProcessConfigurationError('PI_SOURCE must resolve to a PiSourceProvider')
        if not isinstance(self.source_applications, DataSourceApplications):
            raise KpiProcessConfigurationError('source_applications must be DataSourceApplications')
        if (
            not isinstance(self.poll_interval_seconds, int)
            or isinstance(self.poll_interval_seconds, bool)
            or self.poll_interval_seconds <= 0
        ):
            raise KpiProcessConfigurationError(
                'KPI_POLL_INTERVAL_SECONDS must be an integer greater than zero'
            )

    def validate_catalog(self, catalog: KpiCatalog) -> None:
        if not isinstance(catalog, KpiCatalog):
            raise TypeError('catalog must be KpiCatalog')
        try:
            self.source_applications.validate_sources(_catalog_sources(catalog))
        except DataSourceRoutingError as error:
            raise KpiProcessConfigurationError(str(error)) from error

    @classmethod
    def from_configuration(cls, configuration: ResolvedConfiguration) -> KpiProcessSettings:
        if not isinstance(configuration, ResolvedConfiguration):
            raise KpiProcessConfigurationError('configuration must be a ResolvedConfiguration')
        raw_pi_source = configuration.require('PI_SOURCE')
        try:
            pi_source = _PI_SOURCE_VALUES[raw_pi_source]
        except KeyError as error:
            allowed = ', '.join(_PI_SOURCE_VALUES)
            raise KpiProcessConfigurationError(f'PI_SOURCE must be one of: {allowed}') from error
        poll_interval_seconds = configuration.get_int('KPI_POLL_INTERVAL_SECONDS')
        if poll_interval_seconds is None:
            raise KpiProcessConfigurationError(
                'KPI_POLL_INTERVAL_SECONDS must contain an integer value'
            )
        return cls(
            pi_source=pi_source,
            source_applications=DataSourceApplications(
                pi=configuration.require('PI_APPLICATION'),
                dispatch=_optional_application(
                    configuration.get('DISPATCH_APPLICATION'),
                    variable='DISPATCH_APPLICATION',
                ),
                blockgrade=_optional_application(
                    configuration.get('BLOCKGRADE_APPLICATION'),
                    variable='BLOCKGRADE_APPLICATION',
                ),
                remanentes=_optional_application(
                    configuration.get('REMANENTES_APPLICATION'),
                    variable='REMANENTES_APPLICATION',
                ),
                fabrica=_optional_application(
                    configuration.get('FABRICA_APPLICATION'),
                    variable='FABRICA_APPLICATION',
                ),
            ),
            poll_interval_seconds=poll_interval_seconds,
        )


def configuration_specs() -> tuple[ConfigurationVariableSpec, ...]:
    return (
        ConfigurationVariableSpec(key='APPLICATION'),
        ConfigurationVariableSpec(key='VOLUMEN_PATH'),
        ConfigurationVariableSpec(key='PI_SOURCE'),
        ConfigurationVariableSpec(key='PI_APPLICATION'),
        ConfigurationVariableSpec(key='DISPATCH_APPLICATION', required=False),
        ConfigurationVariableSpec(key='BLOCKGRADE_APPLICATION', required=False),
        ConfigurationVariableSpec(key='REMANENTES_APPLICATION', required=False),
        ConfigurationVariableSpec(key='FABRICA_APPLICATION', required=False),
        ConfigurationVariableSpec(
            key='KPI_POLL_INTERVAL_SECONDS',
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


def catalog_sources(catalog: KpiCatalog) -> tuple[DataSource, ...]:
    if not isinstance(catalog, KpiCatalog):
        raise TypeError('catalog must be KpiCatalog')
    return _catalog_sources(catalog)


def _catalog_sources(catalog: KpiCatalog) -> tuple[DataSource, ...]:
    sources: set[DataSource] = set()
    for spec in catalog.specs:
        sources.update(requirement.source for requirement in spec.requirements)
    return tuple(sorted(sources, key=lambda item: item.value))


def _required_application(value: object, *, variable: str) -> str:
    if not isinstance(value, str) or not value:
        raise KpiProcessConfigurationError(f'{variable} must be a non-empty string')
    if value != value.strip():
        raise KpiProcessConfigurationError(f'{variable} must not contain surrounding whitespace')
    return value


def _optional_application(value: object, *, variable: str) -> str | None:
    if value is None or value == '':
        return None
    return _required_application(value, variable=variable)
