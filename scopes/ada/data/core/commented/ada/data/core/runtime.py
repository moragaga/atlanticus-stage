# Espejo pedagógico de los contratos puros compartidos de datos operacionales.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from ada.data.core.contracts import DataPartition, DataSource, DataSourceView
from ada.data.core.errors import DataSourceNotRequestedError


@runtime_checkable
class RuntimeFrameContext(Protocol):
    @property
    def dataframe(self) -> Any: ...

    def last_row(self) -> Any: ...

    def last_value(self, column: str, default: Any = None) -> Any: ...

    def last_value_number(self, column: str, default: float | None = None) -> float | None: ...


@dataclass(frozen=True, slots=True)
class DataRuntimeContext:
    frames: Mapping[DataSourceView, RuntimeFrameContext]

    def __post_init__(self) -> None:
        normalized: dict[DataSourceView, RuntimeFrameContext] = {}
        for view, frame in self.frames.items():
            if not isinstance(view, DataSourceView):
                raise TypeError('data runtime context keys must be DataSourceView values')
            if not isinstance(frame, RuntimeFrameContext):
                raise TypeError(
                    f'{view.source.value}/{view.partition.value}: invalid runtime frame'
                )
            normalized[view] = frame
        object.__setattr__(self, 'frames', MappingProxyType(normalized))

    @property
    def views(self) -> tuple[DataSourceView, ...]:
        return tuple(self.frames)

    @property
    def sources(self) -> tuple[DataSource, ...]:
        return tuple(dict.fromkeys(view.source for view in self.frames))

    def get(self, source: DataSource, partition: DataPartition) -> RuntimeFrameContext:
        if not isinstance(source, DataSource):
            raise TypeError('source must be DataSource')
        if not isinstance(partition, DataPartition):
            raise TypeError('partition must be DataPartition')
        return self.get_view(DataSourceView(source=source, partition=partition))

    def get_view(self, view: DataSourceView) -> RuntimeFrameContext:
        if not isinstance(view, DataSourceView):
            raise TypeError('view must be DataSourceView')
        try:
            return self.frames[view]
        except KeyError as error:
            raise DataSourceNotRequestedError(
                f'{view.source.value}/{view.partition.value}: source view was not requested'
            ) from error
