# Expone el contrato público de la capability KPI Sources.
from ada.kpis.sources.bindings import (
    KpiPartitionBinding,
    KpiSourceBinding,
    KpiSourceRegistry,
    TimePartitionGranularity,
)
from ada.kpis.sources.current import build_current_source_registry
from ada.kpis.sources.errors import (
    KpiSourceBindingError,
    KpiSourceReadError,
    KpiSourceSchemaError,
    KpiSourcesError,
    KpiSourceUnavailableError,
)
from ada.kpis.sources.frame import PandasRuntimeFrameContext
from ada.kpis.sources.loaded import (
    KpiSourceLoadFailure,
    LoadedKpiSources,
    LoadedKpiSourceView,
)
from ada.kpis.sources.loader import KpiSourceLoader
from ada.kpis.sources.operational import KpiOperationalWindow, KpiOperationalWindowResolver
from ada.kpis.sources.pi import PiSourceProvider
from ada.kpis.sources.reader import SourceDatasetReader
from ada.kpis.sources.shifts import MineShiftResolver

__version__ = '0.1.0'

__all__ = [
    'KpiOperationalWindow',
    'KpiOperationalWindowResolver',
    'KpiPartitionBinding',
    'KpiSourceBinding',
    'KpiSourceBindingError',
    'KpiSourceLoadFailure',
    'KpiSourceLoader',
    'KpiSourceReadError',
    'KpiSourceRegistry',
    'KpiSourceSchemaError',
    'KpiSourcesError',
    'KpiSourceUnavailableError',
    'LoadedKpiSourceView',
    'LoadedKpiSources',
    'MineShiftResolver',
    'PandasRuntimeFrameContext',
    'PiSourceProvider',
    'SourceDatasetReader',
    'TimePartitionGranularity',
    '__version__',
    'build_current_source_registry',
]
