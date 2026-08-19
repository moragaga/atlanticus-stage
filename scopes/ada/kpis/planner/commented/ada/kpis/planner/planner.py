# Espejo pedagógico: conserva exactamente el contrato ejecutable y añade contexto en español.
# Se unen columnas y se amplía solo la ventana física; no se altera la vista lógica de cada KPI.
from __future__ import annotations

from collections.abc import Mapping

from ada.kpis.core import KpiCatalog, KpiSource, KpiTimeWindow, ShiftSelection, SourceRequirement
from ada.kpis.planner.models import KpiLoadPlan, KpiSourceLoadPlan


class KpiRequirementPlanner:
    def plan(self, catalog: KpiCatalog) -> KpiLoadPlan:
        if not isinstance(catalog, KpiCatalog):
            raise TypeError('catalog must be KpiCatalog')
        requirements_by_kpi: dict[str, Mapping[KpiSource, SourceRequirement]] = {}
        grouped: dict[KpiSource, list[SourceRequirement]] = {}
        source_order: list[KpiSource] = []
        for spec in catalog.specs:
            requirements = spec.requirements
            requirements_by_kpi[spec.key] = requirements
            for source, requirement in requirements.items():
                if source not in grouped:
                    grouped[source] = []
                    source_order.append(source)
                grouped[source].append(requirement)
        for spec in catalog.over_specs:
            requirements_by_kpi[spec.key] = {}
        sources = tuple(
            _merge_source_requirements(source=source, requirements=grouped[source])
            for source in source_order
        )
        return KpiLoadPlan(sources=sources, requirements_by_kpi=requirements_by_kpi)


def _merge_source_requirements(
    *,
    source: KpiSource,
    requirements: list[SourceRequirement],
) -> KpiSourceLoadPlan:
    snapshot_columns: list[str] = []
    time_columns: list[str] = []
    shift_columns: list[str] = []
    time_window: KpiTimeWindow | None = None
    shifts: list[ShiftSelection] = []
    for requirement in requirements:
        if requirement.time_window is not None:
            _extend_unique(time_columns, requirement.columns)
            time_window = _wider_time_window(time_window, requirement.time_window)
            continue
        if requirement.shift is not None:
            _extend_unique(shift_columns, requirement.columns)
            if requirement.shift not in shifts:
                shifts.append(requirement.shift)
            continue
        _extend_unique(snapshot_columns, requirement.columns)
    return KpiSourceLoadPlan(
        source=source,
        snapshot_columns=tuple(snapshot_columns),
        time_columns=tuple(time_columns),
        time_window=time_window,
        shift_columns=tuple(shift_columns),
        shifts=tuple(shifts),
    )


def _wider_time_window(
    current: KpiTimeWindow | None,
    candidate: KpiTimeWindow,
) -> KpiTimeWindow:
    if current is None or candidate.to_timedelta() > current.to_timedelta():
        return candidate
    return current


def _extend_unique(target: list[str], values: tuple[str, ...]) -> None:
    seen = set(target)
    for value in values:
        if value not in seen:
            target.append(value)
            seen.add(value)
