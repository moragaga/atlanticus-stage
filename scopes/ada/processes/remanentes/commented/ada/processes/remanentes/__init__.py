# La API pública del proceso ADA expone solo catálogo, composición y ejecución.

from ada.processes.remanentes.bootstrap import run
from ada.processes.remanentes.catalog import build_catalog
from ada.processes.remanentes.composition import build_composition

__version__ = '0.1.1'
__all__ = ['__version__', 'build_catalog', 'build_composition', 'run']
