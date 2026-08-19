from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ada.kpis.core import KpiNativeValue
from ada.kpis.evaluation.errors import KpiDependencyNotRequestedError


@dataclass(frozen=True, slots=True)
class KpiDependencies(Mapping[str, KpiNativeValue]):
    data: Mapping[str, KpiNativeValue]

    def __post_init__(self) -> None:
        if not isinstance(self.data, Mapping):
            raise TypeError('data must be a mapping')
        normalized: dict[str, KpiNativeValue] = {}
        for key, value in self.data.items():
            normalized_key = _required_text(key)
            if normalized_key in normalized:
                raise ValueError('dependency keys must be unique')
            normalized[normalized_key] = value
        object.__setattr__(self, 'data', MappingProxyType(normalized))

    def __getitem__(self, key: str) -> KpiNativeValue:
        normalized_key = _required_text(key)
        try:
            return self.data[normalized_key]
        except KeyError as error:
            raise KpiDependencyNotRequestedError(
                f'{normalized_key}: dependency was not requested by this KPI'
            ) from error

    def __iter__(self) -> Iterator[str]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


def _required_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('dependency key must be a non-empty string')
    return value.strip()
