# Espejo pedagógico: esta superficie pública refleja exactamente la API productiva del paquete.
from ada.processes.kpis.bootstrap import load_configuration, run
from ada.processes.kpis.composition import KpiProcessComposition, build_composition
from ada.processes.kpis.job import KpiIterationResult, KpiIterationStatus, KpiProcessJob
from ada.processes.kpis.settings import KpiProcessSettings

__version__ = '0.2.1'

__all__ = [
    'KpiIterationResult',
    'KpiIterationStatus',
    'KpiProcessComposition',
    'KpiProcessJob',
    'KpiProcessSettings',
    '__version__',
    'build_composition',
    'load_configuration',
    'run',
]
