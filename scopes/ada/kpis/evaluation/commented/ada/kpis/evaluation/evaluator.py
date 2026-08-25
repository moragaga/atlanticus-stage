from __future__ import annotations

# Espejo pedagógico: el evaluator planifica requisitos compartidos, carga datos una vez por plan y luego ejecuta la semántica KPI.
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, runtime_checkable

from ada.data.core import (
    DataColumnNotRequestedError,
    DataRuntimeContext,
    DataSource,
    DataSourceNotRequestedError,
)
from ada.data.planner import DataLoadPlan, DataRequirementPlanner
from ada.data.sources import DataSourcesError, LoadedDataSources
from ada.kpis.core import (
    KpiCatalog,
    KpiEvaluation,
    KpiResult,
    KpiSourceTrace,
    KpiSpec,
    KpiStatus,
    KpiWatermark,
    OverKpiSpec,
)
from ada.kpis.evaluation.dependencies import KpiDependencies
from ada.kpis.evaluation.errors import KpiInvalidValueError
from ada.kpis.evaluation.modes import resolve_base_value
from ada.kpis.evaluation.values import build_result, error_result


@runtime_checkable
class KpiEvaluationSourceLoader(Protocol):
    def load(self, *, plan: DataLoadPlan, as_of: datetime) -> LoadedDataSources: ...


class KpiEvaluator:
    def __init__(
        self,
        *,
        source_loader: KpiEvaluationSourceLoader,
        planner: DataRequirementPlanner | None = None,
    ) -> None:
        if not isinstance(source_loader, KpiEvaluationSourceLoader):
            raise TypeError('source_loader must implement KpiEvaluationSourceLoader')
        if planner is not None and not isinstance(planner, DataRequirementPlanner):
            raise TypeError('planner must be DataRequirementPlanner or None')
        self._source_loader = source_loader
        self._planner = planner or DataRequirementPlanner()

    def evaluate(
        self,
        *,
        catalog: KpiCatalog,
        watermark: KpiWatermark,
        source_watermarks: Mapping[DataSource, KpiWatermark | None] | None = None,
    ) -> KpiEvaluation:
        if not isinstance(catalog, KpiCatalog):
            raise TypeError('catalog must be KpiCatalog')
        if not isinstance(watermark, KpiWatermark):
            raise TypeError('watermark must be KpiWatermark')
        watermarks = _normalize_source_watermarks(source_watermarks)
        requirements_by_key = {spec.key: spec.requirements for spec in catalog.specs}
        requirements_by_key.update({spec.key: () for spec in catalog.over_specs})
        plan = self._planner.plan(requirements_by_key)
        loaded = self._source_loader.load(plan=plan, as_of=watermark.timestamp_utc)
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
            KpiSourceTrace(source=source, watermark=watermarks.get(source))
            for source in plan.sources
        )
        return KpiEvaluation(
            watermark=watermark,
            results=tuple(results),
            sources=sources,
        )

    @staticmethod
    def _evaluate_base(*, spec: KpiSpec, loaded: LoadedDataSources) -> KpiResult:
        try:
            data_context = loaded.context_for(spec.key)
            if not isinstance(data_context, DataRuntimeContext):
                raise TypeError('loaded source context must be DataRuntimeContext')
            value = resolve_base_value(spec=spec, data_context=data_context)
        except KpiInvalidValueError as error:
            return error_result(
                key=spec.key,
                area=spec.area,
                value_kind=spec.value_kind,
                persist_history=spec.persist_history,
                error=_safe_error(error, include_message=True),
            )
        except DataSourcesError as error:
            return error_result(
                key=spec.key,
                area=spec.area,
                value_kind=spec.value_kind,
                persist_history=spec.persist_history,
                error=_safe_error(error, include_message=True),
            )
        except (DataSourceNotRequestedError, DataColumnNotRequestedError) as error:
            return error_result(
                key=spec.key,
                area=spec.area,
                value_kind=spec.value_kind,
                persist_history=spec.persist_history,
                error=_safe_error(error, include_message=True),
            )
        except Exception as error:
            return error_result(
                key=spec.key,
                area=spec.area,
                value_kind=spec.value_kind,
                persist_history=spec.persist_history,
                error=_safe_error(error, include_message=False),
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
        failed_dependencies = tuple(
            key for key in spec.dependencies if resolved[key].status is KpiStatus.ERROR
        )
        if failed_dependencies:
            return error_result(
                key=spec.key,
                area=spec.area,
                value_kind=spec.value_kind,
                persist_history=spec.persist_history,
                error=f'KPI dependency failed: {", ".join(failed_dependencies)}',
            )
        values = KpiDependencies({key: resolved[key].value for key in spec.dependencies})
        try:
            value = spec.resolver(values)
        except KpiInvalidValueError as error:
            return error_result(
                key=spec.key,
                area=spec.area,
                value_kind=spec.value_kind,
                persist_history=spec.persist_history,
                error=_safe_error(error, include_message=True),
            )
        except Exception as error:
            return error_result(
                key=spec.key,
                area=spec.area,
                value_kind=spec.value_kind,
                persist_history=spec.persist_history,
                error=_safe_error(error, include_message=False),
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


def _safe_error(error: Exception, *, include_message: bool) -> str:
    error_type = type(error).__name__
    if not include_message:
        return error_type
    message = str(error).strip()
    if not message:
        return error_type
    cleaned = ' '.join(message.split())
    if len(cleaned) > 400:
        cleaned = f'{cleaned[:400]}...<truncated>'
    return f'{error_type}: {cleaned}'


def _normalize_source_watermarks(
    value: Mapping[DataSource, KpiWatermark | None] | None,
) -> Mapping[DataSource, KpiWatermark | None]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError('source_watermarks must be a mapping or None')
    normalized: dict[DataSource, KpiWatermark | None] = {}
    for source, watermark in value.items():
        if not isinstance(source, DataSource):
            raise TypeError('source_watermarks keys must be DataSource values')
        if watermark is not None and not isinstance(watermark, KpiWatermark):
            raise TypeError(f'{source.value}: source watermark must be KpiWatermark or None')
        normalized[source] = watermark
    return normalized
