from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

ScopeValue = int | str


@dataclass(frozen=True, slots=True)
class SourceScopeItem:
    value: ScopeValue
    partition: Mapping[str, str]

    def __post_init__(self) -> None:
        value = _scope_value(self.value)
        if not isinstance(self.partition, Mapping) or not self.partition:
            raise ValueError('partition must be a non-empty mapping')
        partition = {
            _required_text(key, 'partition key'): _required_text(item, 'partition value')
            for key, item in self.partition.items()
        }
        object.__setattr__(self, 'value', value)
        object.__setattr__(self, 'partition', MappingProxyType(partition))


@dataclass(frozen=True, slots=True)
class SourceScope:
    token: str
    items: tuple[SourceScopeItem, ...]

    def __post_init__(self) -> None:
        token = _required_text(self.token, 'token')
        items = tuple(self.items)
        if not items or not all(isinstance(item, SourceScopeItem) for item in items):
            raise ValueError('items must contain SourceScopeItem values')
        values = tuple(item.value for item in items)
        if len(set(values)) != len(values):
            raise ValueError('scope values must be unique')
        object.__setattr__(self, 'token', token)
        object.__setattr__(self, 'items', items)

    @property
    def values(self) -> tuple[ScopeValue, ...]:
        return tuple(item.value for item in self.items)


def _scope_value(value: object) -> ScopeValue:
    if isinstance(value, bool):
        raise ValueError('scope value must be an integer or non-empty string')
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError('scope value must be an integer or non-empty string')


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value.strip()
