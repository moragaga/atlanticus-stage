# Rutea aplicaciones por source lógico; Fábrica planes y KPIs comparten FABRICA_APPLICATION.
from __future__ import annotations

from dataclasses import dataclass

from ada.kpis.core import KpiCatalog, KpiSource
from ada.kpis.sources import PiSourceProvider
from ada.processes.kpis.errors import KpiProcessConfigurationError
from atlanticus.configuration import ConfigurationVariableSpec, ResolvedConfiguration

_PI_SOURCE_VALUES = {
    'PI_WEB_API': PiSourceProvider.PI_WEB_API,
    'NOTPII': PiSourceProvider.NOTPII,
}
_DEFAULT_POLL_INTERVAL_SECONDS = 1
_SOURCE_APPLICATION_VARIABLES = {
    KpiSource.PI_INTERPOLATED: 'PI_APPLICATION',
    KpiSource.PI_RECORDED: 'PI_APPLICATION',
    KpiSource.DISPATCH_TIEMPOS_MLP: 'DISPATCH_APPLICATION',
    KpiSource.DISPATCH_STD_SHIFT_LOADS: 'DISPATCH_APPLICATION',
    KpiSource.DISPATCH_STD_SHIFT_STATE: 'DISPATCH_APPLICATION',
    KpiSource.DISPATCH_STD_TRUCK: 'DISPATCH_APPLICATION',
    KpiSource.DISPATCH_STD_SHIFT_GRADE: 'DISPATCH_APPLICATION',
    KpiSource.DISPATCH_STD_SHIFT_LOADS_2: 'DISPATCH_APPLICATION',
    KpiSource.DISPATCH_STD_SHIFT_DUMPS: 'DISPATCH_APPLICATION',
    KpiSource.BLOCKGRADE_MMS_BLOCKGRADE_DETAILS_BUCKET: 'BLOCKGRADE_APPLICATION',
    KpiSource.REMANENTES_EXTRAIBLES: 'REMANENTES_APPLICATION',
    KpiSource.REMANENTES_NO_EXTRAIBLES: 'REMANENTES_APPLICATION',
    KpiSource.REMANENTES_STOCKS: 'REMANENTES_APPLICATION',
    KpiSource.FABRICA_PLANES: 'FABRICA_APPLICATION',
    KpiSource.FABRICA_KPIS: 'FABRICA_APPLICATION',
}


@dataclass(frozen=True, slots=True)
class KpiSourceApplications:
    pi: str
    dispatch: str | None = None
    blockgrade: str | None = None
    remanentes: str | None = None
    fabrica: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'pi', _required_application(self.pi, variable='PI_APPLICATION'))
        for field_name, variable in (
            ('dispatch', 'DISPATCH_APPLICATION'),
            ('blockgrade', 'BLOCKGRADE_APPLICATION'),
            ('remanentes', 'REMANENTES_APPLICATION'),
            ('fabrica', 'FABRICA_APPLICATION'),
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _required_application(value, variable=variable),
                )

    def application_for(self, source: KpiSource) -> str:
        if not isinstance(source, KpiSource):
            raise TypeError('source must be KpiSource')
        try:
            variable = _SOURCE_APPLICATION_VARIABLES[source]
        except KeyError as error:
            raise KpiProcessConfigurationError(
                f'{source.value}: KPI source has no application routing contract'
            ) from error
        value = {
            'PI_APPLICATION': self.pi,
            'DISPATCH_APPLICATION': self.dispatch,
            'BLOCKGRADE_APPLICATION': self.blockgrade,
            'REMANENTES_APPLICATION': self.remanentes,
            'FABRICA_APPLICATION': self.fabrica,
        }[variable]
        if value is None:
            raise KpiProcessConfigurationError(
                f'{variable} is required by configured KPI source {source.value}'
            )
        return value

    def validate_catalog(self, catalog: KpiCatalog) -> None:
        if not isinstance(catalog, KpiCatalog):
            raise TypeError('catalog must be KpiCatalog')
        for source in _catalog_sources(catalog):
            self.application_for(source)


@dataclass(frozen=True, slots=True)
class KpiProcessSettings:
    pi_source: PiSourceProvider
    source_applications: KpiSourceApplications
    poll_interval_seconds: int = _DEFAULT_POLL_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.pi_source, PiSourceProvider):
            raise KpiProcessConfigurationError('PI_SOURCE must resolve to a PiSourceProvider')
        if not isinstance(self.source_applications, KpiSourceApplications):
            raise KpiProcessConfigurationError('source_applications must be KpiSourceApplications')
        if (
            not isinstance(self.poll_interval_seconds, int)
            or isinstance(self.poll_interval_seconds, bool)
            or self.poll_interval_seconds <= 0
        ):
            raise KpiProcessConfigurationError(
                'KPI_POLL_INTERVAL_SECONDS must be an integer greater than zero'
            )

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
            source_applications=KpiSourceApplications(
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


def catalog_sources(catalog: KpiCatalog) -> tuple[KpiSource, ...]:
    if not isinstance(catalog, KpiCatalog):
        raise TypeError('catalog must be KpiCatalog')
    return _catalog_sources(catalog)


def _catalog_sources(catalog: KpiCatalog) -> tuple[KpiSource, ...]:
    sources: set[KpiSource] = set()
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
