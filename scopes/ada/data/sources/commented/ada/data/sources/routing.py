# Espejo pedagógico de bindings, routing y carga física de datos operacionales.
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ada.data.core import DataSource
from ada.data.sources.errors import DataSourceRoutingError

_SOURCE_APPLICATION_FIELDS = {
    DataSource.PI_INTERPOLATED: 'pi',
    DataSource.PI_RECORDED: 'pi',
    DataSource.DISPATCH_TIEMPOS_MLP: 'dispatch',
    DataSource.DISPATCH_STD_SHIFT_LOADS: 'dispatch',
    DataSource.DISPATCH_STD_SHIFT_STATE: 'dispatch',
    DataSource.DISPATCH_STD_TRUCK: 'dispatch',
    DataSource.DISPATCH_STD_SHIFT_GRADE: 'dispatch',
    DataSource.DISPATCH_STD_SHIFT_LOADS_2: 'dispatch',
    DataSource.DISPATCH_STD_SHIFT_DUMPS: 'dispatch',
    DataSource.BLOCKGRADE_MMS_BLOCKGRADE_DETAILS_BUCKET: 'blockgrade',
    DataSource.REMANENTES_EXTRAIBLES: 'remanentes',
    DataSource.REMANENTES_NO_EXTRAIBLES: 'remanentes',
    DataSource.REMANENTES_STOCKS: 'remanentes',
    DataSource.FABRICA_PLANES: 'fabrica',
    DataSource.FABRICA_KPIS: 'fabrica',
}


@dataclass(frozen=True, slots=True)
class DataSourceApplications:
    pi: str
    dispatch: str | None = None
    blockgrade: str | None = None
    remanentes: str | None = None
    fabrica: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'pi', _required_application(self.pi, field_name='pi'))
        for field_name in ('dispatch', 'blockgrade', 'remanentes', 'fabrica'):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _required_application(value, field_name=field_name),
                )

    def application_for(self, source: DataSource) -> str:
        if not isinstance(source, DataSource):
            raise TypeError('source must be DataSource')
        try:
            field_name = _SOURCE_APPLICATION_FIELDS[source]
        except KeyError as error:
            raise DataSourceRoutingError(
                f'{source.value}: data source has no application routing contract'
            ) from error
        value = getattr(self, field_name)
        if value is None:
            raise DataSourceRoutingError(
                f'{source.value}: data source application route is not configured'
            )
        return value

    def validate_sources(self, sources: Iterable[DataSource]) -> None:
        for source in sources:
            self.application_for(source)


def _required_application(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise DataSourceRoutingError(f'{field_name} application must be a non-empty string')
    if value != value.strip():
        raise DataSourceRoutingError(
            f'{field_name} application must not contain surrounding whitespace'
        )
    return value
