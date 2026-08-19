# Expone la API pública estable del core de KPI de ADA.
# El core solo contiene contratos y no conoce infraestructura, datasets físicos ni procesos.
from ada.kpis.core.catalog import KpiCatalog
from ada.kpis.core.enums import KpiArea, KpiMode, KpiSource, KpiStatus, KpiValueKind, ShiftScope
from ada.kpis.core.requirements import (
    KpiTimeWindow,
    KpiTimeWindowUnit,
    ShiftSelection,
    SourceRequirement,
)
from ada.kpis.core.results import KpiEvaluation, KpiResult, KpiSourceTrace
from ada.kpis.core.rules import KpiResolver, KpiSpec, OverKpiResolver, OverKpiSpec
from ada.kpis.core.runtime import (
    DataRuntimeContext,
    KpiColumnNotRequestedError,
    KpiSourceNotRequestedError,
    RuntimeFrameContext,
)
from ada.kpis.core.values import KpiJsonContainer, KpiJsonValue, KpiNativeValue, KpiScalar
from ada.kpis.core.watermark import KpiWatermark

__all__ = [
    'DataRuntimeContext',
    'KpiArea',
    'KpiCatalog',
    'KpiColumnNotRequestedError',
    'KpiEvaluation',
    'KpiJsonContainer',
    'KpiJsonValue',
    'KpiMode',
    'KpiNativeValue',
    'KpiResolver',
    'KpiResult',
    'KpiScalar',
    'KpiSource',
    'KpiSourceNotRequestedError',
    'KpiSourceTrace',
    'KpiSpec',
    'KpiStatus',
    'KpiTimeWindow',
    'KpiTimeWindowUnit',
    'KpiValueKind',
    'KpiWatermark',
    'OverKpiResolver',
    'OverKpiSpec',
    'RuntimeFrameContext',
    'ShiftScope',
    'ShiftSelection',
    'SourceRequirement',
]
