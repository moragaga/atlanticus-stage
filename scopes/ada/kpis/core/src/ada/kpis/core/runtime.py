from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from ada.kpis.core.enums import KpiSource


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
    frames: Mapping[KpiSource, RuntimeFrameContext]

    def __post_init__(self) -> None:
        normalized: dict[KpiSource, RuntimeFrameContext] = {}
        for source, frame in self.frames.items():
            if not isinstance(source, KpiSource):
                raise TypeError('data runtime context sources must be KpiSource values')
            if not isinstance(frame, RuntimeFrameContext):
                raise TypeError(f'{source.value}: frame must implement RuntimeFrameContext')
            normalized[source] = frame
        object.__setattr__(self, 'frames', MappingProxyType(normalized))

    @property
    def sources(self) -> tuple[KpiSource, ...]:
        return tuple(self.frames)

    def get(self, source: KpiSource) -> RuntimeFrameContext:
        if not isinstance(source, KpiSource):
            raise TypeError('source must be KpiSource')
        try:
            return self.frames[source]
        except KeyError as error:
            raise KpiSourceNotRequestedError(
                f'{source.value}: source was not requested by this KPI'
            ) from error
