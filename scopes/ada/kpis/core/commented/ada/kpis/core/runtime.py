# Entrega al resolver únicamente las vistas exactas solicitadas por source y partition.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from ada.kpis.core.enums import KpiPartition, KpiSource
from ada.kpis.core.requirements import KpiSourceView


class KpiSourceNotRequestedError(KeyError):
    pass


class KpiColumnNotRequestedError(KeyError):
    pass


@runtime_checkable
class RuntimeFrameContext(Protocol):
    @property
    def dataframe(self) -> Any: ...

    def last_row(self) -> Any: ...

    def last_value(self, column: str, default: Any = None) -> Any: ...

    def last_value_number(self, column: str, default: float | None = None) -> float | None: ...


@dataclass(frozen=True, slots=True)
class DataRuntimeContext:
    frames: Mapping[KpiSourceView, RuntimeFrameContext]

    def __post_init__(self) -> None:
        normalized: dict[KpiSourceView, RuntimeFrameContext] = {}
        for view, frame in self.frames.items():
            if not isinstance(view, KpiSourceView):
                raise TypeError('data runtime context keys must be KpiSourceView values')
            if not isinstance(frame, RuntimeFrameContext):
                raise TypeError(f'{view.source.value}/{view.partition.value}: invalid runtime frame')
            normalized[view] = frame
        object.__setattr__(self, 'frames', MappingProxyType(normalized))

    @property
    def views(self) -> tuple[KpiSourceView, ...]:
        return tuple(self.frames)

    @property
    def sources(self) -> tuple[KpiSource, ...]:
        return tuple(dict.fromkeys(view.source for view in self.frames))

    def get(self, source: KpiSource, partition: KpiPartition) -> RuntimeFrameContext:
        if not isinstance(source, KpiSource):
            raise TypeError('source must be KpiSource')
        if not isinstance(partition, KpiPartition):
            raise TypeError('partition must be KpiPartition')
        return self.get_view(KpiSourceView(source=source, partition=partition))

    def get_view(self, view: KpiSourceView) -> RuntimeFrameContext:
        if not isinstance(view, KpiSourceView):
            raise TypeError('view must be KpiSourceView')
        try:
            return self.frames[view]
        except KeyError as error:
            raise KpiSourceNotRequestedError(
                f'{view.source.value}/{view.partition.value}: source view was not requested by this KPI'
            ) from error
