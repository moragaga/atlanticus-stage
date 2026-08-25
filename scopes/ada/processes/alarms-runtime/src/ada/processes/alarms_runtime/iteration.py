from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from ada.alarms.core import AlarmIdentity
from ada.data.core import DataRuntimeContext, normalize_utc_second
from ada.data.planner import DataLoadPlan
from ada.data.sources import LoadedDataSources
from ada.processes.alarms_runtime.session import AlarmExecutionSession


class AlarmExecutionIterationError(ValueError):
    pass


@runtime_checkable
class AlarmIterationSourceLoader(Protocol):
    def load(self, *, plan: DataLoadPlan, as_of: datetime) -> LoadedDataSources: ...


@dataclass(frozen=True, slots=True)
class AlarmExecutionIteration:
    session: AlarmExecutionSession
    loaded_sources: LoadedDataSources

    def __post_init__(self) -> None:
        if not isinstance(self.session, AlarmExecutionSession):
            raise TypeError('session must be AlarmExecutionSession')
        if not isinstance(self.loaded_sources, LoadedDataSources):
            raise TypeError('loaded_sources must be LoadedDataSources')
        if self.loaded_sources.plan != self.session.data_plan:
            raise AlarmExecutionIterationError(
                'loaded source plan must match the execution session data plan'
            )

    @property
    def as_of(self) -> datetime:
        return self.loaded_sources.as_of

    def data_for(self, identity: AlarmIdentity) -> DataRuntimeContext:
        entry = self.session.entry_for(identity)
        context = self.loaded_sources.context_for(entry.identity.canonical_key)
        if not isinstance(context, DataRuntimeContext):
            raise TypeError('loaded source context must be DataRuntimeContext')
        return context


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
        normalized_as_of = normalize_utc_second(as_of, field_name='as_of')
        loaded = self.source_loader.load(
            plan=self.session.data_plan,
            as_of=normalized_as_of,
        )
        if not isinstance(loaded, LoadedDataSources):
            raise TypeError('source_loader must return LoadedDataSources')
        if loaded.as_of != normalized_as_of:
            raise AlarmExecutionIterationError(
                'loaded source as_of must match the requested iteration as_of'
            )
        return AlarmExecutionIteration(
            session=self.session,
            loaded_sources=loaded,
        )
