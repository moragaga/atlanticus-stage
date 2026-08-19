# Agrega los specs declarados por los dominios del catálogo sin autodiscovery del filesystem.
# KpiCatalog conserva la validación contractual de claves y del orden explícito de dependencias Over.
from __future__ import annotations

from ada.kpis.core import KpiCatalog, KpiSpec, OverKpiSpec
from ada.processes.kpis.catalog.general.over.specs import OVER_SPECS as GENERAL_OVER_SPECS
from ada.processes.kpis.catalog.general.specs import SPECS as GENERAL_SPECS
from ada.processes.kpis.errors import KpiProcessCatalogError

KPI_SPECS: tuple[KpiSpec, ...] = (*GENERAL_SPECS,)
OVER_KPI_SPECS: tuple[OverKpiSpec, ...] = (*GENERAL_OVER_SPECS,)


def build_catalog() -> KpiCatalog:
    if not KPI_SPECS and not OVER_KPI_SPECS:
        raise KpiProcessCatalogError('KPI catalog requires at least one configured KPI')
    try:
        return KpiCatalog(specs=KPI_SPECS, over_specs=OVER_KPI_SPECS)
    except (TypeError, ValueError) as error:
        raise KpiProcessCatalogError(str(error)) from error
