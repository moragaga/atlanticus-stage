from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from ada.alarms.core import AlarmIdentity
from ada.data.core import DataRuntimeContext, normalize_utc_second
from ada.data.planner import DataLoadPlan
from ada.data.sources import LoadedDataSources
from ada.processes.alarms_runtime.session import AlarmExecutionSession


# Los errores de esta capa representan una inconsistencia estructural de la iteración.
# No convierten fallos de una fuente concreta en semántica de alarma; LoadedDataSources conserva
# esa separación y data_for() deja que el consumidor correspondiente observe el fallo.
class AlarmExecutionIterationError(ValueError):
    pass


# El proceso depende del contrato de carga, no de un cliente físico concreto. En ejecución real
# la implementación será DataSourceLoader; en pruebas puede inyectarse un loader controlado.
@runtime_checkable
class AlarmIterationSourceLoader(Protocol):
    def load(self, *, plan: DataLoadPlan, as_of: datetime) -> LoadedDataSources: ...


# Una iteración une la sesión congelada con un snapshot fresco de inputs. No duplica as_of:
# LoadedDataSources ya es la autoridad del instante con que se resolvieron ventanas y turnos.
@dataclass(frozen=True, slots=True)
class AlarmExecutionIteration:
    session: AlarmExecutionSession
    loaded_sources: LoadedDataSources

    def __post_init__(self) -> None:
        if not isinstance(self.session, AlarmExecutionSession):
            raise TypeError('session must be AlarmExecutionSession')
        if not isinstance(self.loaded_sources, LoadedDataSources):
            raise TypeError('loaded_sources must be LoadedDataSources')
        # El loader nunca puede devolver datos preparados con otro plan. Mezclar planes rompería
        # la garantía de que configuración, evaluator requirements y source projection son una unidad.
        if self.loaded_sources.plan != self.session.data_plan:
            raise AlarmExecutionIterationError(
                'loaded source plan must match the execution session data plan'
            )

    @property
    def as_of(self) -> datetime:
        return self.loaded_sources.as_of

    def data_for(self, identity: AlarmIdentity) -> DataRuntimeContext:
        # entry_for() valida primero que la alarma pertenece a esta sesión y evita pedir contextos
        # de consumidores ajenos usando sólo un string coincidente.
        entry = self.session.entry_for(identity)
        context = self.loaded_sources.context_for(entry.identity.canonical_key)
        if not isinstance(context, DataRuntimeContext):
            raise TypeError('loaded source context must be DataRuntimeContext')
        return context


# El objeto conserva únicamente dependencias estáticas. Deliberadamente NO guarda el resultado
# anterior: cada llamada a load() representa una vuelta operacional y debe releer las fuentes.
@dataclass(slots=True)
class AlarmIterationLoader:
    session: AlarmExecutionSession
    source_loader: AlarmIterationSourceLoader

    def __post_init__(self) -> None:
        if not isinstance(self.session, AlarmExecutionSession):
            raise TypeError('session must be AlarmExecutionSession')
        if not isinstance(self.source_loader, AlarmIterationSourceLoader):
            raise TypeError('source_loader must implement AlarmIterationSourceLoader')

    def load(self, *, as_of: datetime) -> AlarmExecutionIteration:
        # El llamador congela un solo now por iteración. Normalizar antes del I/O impide que distintas
        # fuentes resuelvan ventanas con precisiones o zonas horarias diferentes.
        normalized_as_of = normalize_utc_second(as_of, field_name='as_of')
        # Esta llamada ocurre siempre, incluso si la vuelta anterior usó el mismo as_of. No hay cache.
        loaded = self.source_loader.load(
            plan=self.session.data_plan,
            as_of=normalized_as_of,
        )
        if not isinstance(loaded, LoadedDataSources):
            raise TypeError('source_loader must return LoadedDataSources')
        # Un adapter defectuoso no puede sustituir silenciosamente el instante de la iteración.
        if loaded.as_of != normalized_as_of:
            raise AlarmExecutionIterationError(
                'loaded source as_of must match the requested iteration as_of'
            )
        return AlarmExecutionIteration(
            session=self.session,
            loaded_sources=loaded,
        )
