from __future__ import annotations

from ada.kpis.core import KpiCatalog, KpiSourceView, SourceRequirement
from ada.kpis.planner.models import KpiLoadPlan, KpiSourceViewLoadPlan


class KpiRequirementPlanner:
    def plan(self, catalog: KpiCatalog) -> KpiLoadPlan:
        if not isinstance(catalog, KpiCatalog):
            raise TypeError('catalog must be KpiCatalog')
        requirements_by_kpi: dict[str, tuple[SourceRequirement, ...]] = {}
        grouped: dict[KpiSourceView, list[SourceRequirement]] = {}
        view_order: list[KpiSourceView] = []
        for spec in catalog.specs:
            requirements = spec.requirements
            requirements_by_kpi[spec.key] = requirements
            for requirement in requirements:
                view = requirement.view
                if view not in grouped:
                    grouped[view] = []
                    view_order.append(view)
                grouped[view].append(requirement)
        for spec in catalog.over_specs:
            requirements_by_kpi[spec.key] = ()
        views = tuple(
            _merge_view_requirements(view=view, requirements=grouped[view]) for view in view_order
        )
        return KpiLoadPlan(views=views, requirements_by_kpi=requirements_by_kpi)


def _merge_view_requirements(
    *,
    view: KpiSourceView,
    requirements: list[SourceRequirement],
) -> KpiSourceViewLoadPlan:
    columns: list[str] = []
    time_windows = []
    operational_scopes = []
    shifts = []
    for requirement in requirements:
        _extend_unique(columns, requirement.columns)
        if requirement.time_window is not None and requirement.time_window not in time_windows:
            time_windows.append(requirement.time_window)
        if (
            requirement.operational_scope is not None
            and requirement.operational_scope not in operational_scopes
        ):
            operational_scopes.append(requirement.operational_scope)
        if requirement.shift is not None and requirement.shift not in shifts:
            shifts.append(requirement.shift)
    return KpiSourceViewLoadPlan(
        view=view,
        columns=tuple(columns),
        time_windows=tuple(time_windows),
        operational_scopes=tuple(operational_scopes),
        shifts=tuple(shifts),
    )


def _extend_unique(target: list[str], values: tuple[str, ...]) -> None:
    seen = set(target)
    for value in values:
        if value not in seen:
            target.append(value)
            seen.add(value)
