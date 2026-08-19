# Orquestador reusable de una evaluación KPI completa para un watermark.
# Planifica, carga fuentes, ejecuta base/over y retorna KpiEvaluation sin persistir.
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from ada.kpis.core import (
    DataRuntimeContext,
    KpiCatalog,
    KpiEvaluation,
    KpiResult,
    KpiSource,
    KpiSourceTrace,
    KpiSpec,
    KpiStatus,
    KpiWatermark,
    OverKpiSpec,
)
from ada.kpis.evaluation.dependencies import KpiDependencies
from ada.kpis.evaluation.errors import KpiInvalidValueError
from ada.kpis.evaluation.modes import resolve_base_value
from ada.kpis.evaluation.values import build_result, status_result
from ada.kpis.planner import KpiLoadPlan, KpiRequirementPlanner
from ada.kpis.sources import LoadedKpiSources


@runtime_checkable
# Contrato estructural para inyectar Sources sin acoplarse a composición de proceso.
class KpiEvaluationSourceLoader(Protocol):
    def load(self, *, plan: KpiLoadPlan, watermark: KpiWatermark) -> LoadedKpiSources: ...


# Coordina una evaluación completa manteniendo aislamiento por KPI.
class KpiEvaluator:
    def __init__(
        self,
        *,
        source_loader: KpiEvaluationSourceLoader,
        planner: KpiRequirementPlanner | None = None,
    ) -> None:
        if not isinstance(source_loader, KpiEvaluationSourceLoader):
            raise TypeError('source_loader must implement KpiEvaluationSourceLoader')
        if planner is not None and not isinstance(planner, KpiRequirementPlanner):
            raise TypeError('planner must be KpiRequirementPlanner or None')
        self._source_loader = source_loader
        self._planner = planner or KpiRequirementPlanner()

    def evaluate(
        self,
        *,
        catalog: KpiCatalog,
        watermark: KpiWatermark,
        source_watermarks: Mapping[KpiSource, KpiWatermark | None] | None = None,
    ) -> KpiEvaluation:
        if not isinstance(catalog, KpiCatalog):
            raise TypeError('catalog must be KpiCatalog')
        if not isinstance(watermark, KpiWatermark):
            raise TypeError('watermark must be KpiWatermark')
        watermarks = _normalize_source_watermarks(source_watermarks)
        plan = self._planner.plan(catalog)
        loaded = self._source_loader.load(plan=plan, watermark=watermark)
        results: list[KpiResult] = []
        resolved: dict[str, KpiResult] = {}
        for spec in catalog.specs:
            result = self._evaluate_base(spec=spec, loaded=loaded)
            results.append(result)
            resolved[spec.key] = result
        for spec in catalog.over_specs:
            result = self._evaluate_over(spec=spec, resolved=resolved)
            results.append(result)
            resolved[spec.key] = result
        sources = tuple(
            KpiSourceTrace(source=source_plan.source, watermark=watermarks.get(source_plan.source))
            for source_plan in plan.sources
        )
        return KpiEvaluation(
            watermark=watermark,
            results=tuple(results),
            sources=sources,
        )

    @staticmethod
    def _evaluate_base(*, spec: KpiSpec, loaded: LoadedKpiSources) -> KpiResult:
        try:
            data_context = loaded.context_for(spec.key)
            if not isinstance(data_context, DataRuntimeContext):
                raise TypeError('loaded source context must be DataRuntimeContext')
            value = resolve_base_value(spec=spec, data_context=data_context)
        except KpiInvalidValueError:
            return status_result(
                key=spec.key,
                area=spec.area,
                value_kind=spec.value_kind,
                persist_history=spec.persist_history,
                status=KpiStatus.INVALID,
            )
        except Exception:
            return status_result(
                key=spec.key,
                area=spec.area,
                value_kind=spec.value_kind,
                persist_history=spec.persist_history,
                status=KpiStatus.ERROR,
            )
        return build_result(
            key=spec.key,
            area=spec.area,
            value_kind=spec.value_kind,
            persist_history=spec.persist_history,
            decimals=spec.decimals,
            is_truncated=spec.is_truncated,
            value=value,
        )

    @staticmethod
    def _evaluate_over(*, spec: OverKpiSpec, resolved: Mapping[str, KpiResult]) -> KpiResult:
        dependencies = tuple(resolved[key] for key in spec.dependencies)
        propagated = _propagated_status(dependencies)
        if propagated is not None:
            return status_result(
                key=spec.key,
                area=spec.area,
                value_kind=spec.value_kind,
                persist_history=spec.persist_history,
                status=propagated,
            )
        values = KpiDependencies({key: resolved[key].value for key in spec.dependencies})
        try:
            value = spec.resolver(values)
        except KpiInvalidValueError:
            return status_result(
                key=spec.key,
                area=spec.area,
                value_kind=spec.value_kind,
                persist_history=spec.persist_history,
                status=KpiStatus.INVALID,
            )
        except Exception:
            return status_result(
                key=spec.key,
                area=spec.area,
                value_kind=spec.value_kind,
                persist_history=spec.persist_history,
                status=KpiStatus.ERROR,
            )
        return build_result(
            key=spec.key,
            area=spec.area,
            value_kind=spec.value_kind,
            persist_history=spec.persist_history,
            decimals=spec.decimals,
            is_truncated=spec.is_truncated,
            value=value,
        )


# Precedencia de estados para OverKpiSpec: ERROR > INVALID > MISSING.
def _propagated_status(results: tuple[KpiResult, ...]) -> KpiStatus | None:
    for status in (KpiStatus.ERROR, KpiStatus.INVALID, KpiStatus.MISSING):
        if any(result.status is status for result in results):
            return status
    return None


# La metadata de fuentes se recibe congelada desde la composición del tick.
def _normalize_source_watermarks(
    value: Mapping[KpiSource, KpiWatermark | None] | None,
) -> Mapping[KpiSource, KpiWatermark | None]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError('source_watermarks must be a mapping or None')
    normalized: dict[KpiSource, KpiWatermark | None] = {}
    for source, watermark in value.items():
        if not isinstance(source, KpiSource):
            raise TypeError('source_watermarks keys must be KpiSource values')
        if watermark is not None and not isinstance(watermark, KpiWatermark):
            raise TypeError(f'{source.value}: source watermark must be KpiWatermark or None')
        normalized[source] = watermark
    return normalized
