from __future__ import annotations

from dataclasses import dataclass

from ada.kpis.core.rules import KpiSpec, OverKpiSpec


@dataclass(frozen=True, slots=True)
class KpiCatalog:
    specs: tuple[KpiSpec, ...]
    over_specs: tuple[OverKpiSpec, ...] = ()

    def __post_init__(self) -> None:
        specs = tuple(self.specs)
        over_specs = tuple(self.over_specs)
        if not specs and not over_specs:
            raise ValueError('kpi catalog requires at least one spec')
        if not all(isinstance(spec, KpiSpec) for spec in specs):
            raise TypeError('specs must contain KpiSpec values')
        if not all(isinstance(spec, OverKpiSpec) for spec in over_specs):
            raise TypeError('over_specs must contain OverKpiSpec values')
        all_keys = [spec.key for spec in specs]
        all_keys.extend(spec.key for spec in over_specs)
        duplicates = _duplicates(all_keys)
        if duplicates:
            raise ValueError(f'kpi catalog contains duplicate keys: {duplicates}')
        available = {spec.key for spec in specs}
        for spec in over_specs:
            missing = tuple(key for key in spec.dependencies if key not in available)
            if missing:
                raise ValueError(
                    f'{spec.key}: over dependencies must reference base or prior over kpis; '
                    f'missing={missing}'
                )
            available.add(spec.key)
        object.__setattr__(self, 'specs', specs)
        object.__setattr__(self, 'over_specs', over_specs)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(spec.key for spec in self.specs) + tuple(spec.key for spec in self.over_specs)

    @property
    def persisted_history_keys(self) -> tuple[str, ...]:
        return tuple(spec.key for spec in (*self.specs, *self.over_specs) if spec.persist_history)


def _duplicates(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return tuple(duplicates)
