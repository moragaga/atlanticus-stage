# El provider protege al resto del proceso de catálogos incompletos o con una fuente incorrecta.
# Para este proceso interpolation_seconds es obligatorio incluso si temporalmente
# solo hay tags recorded,
# porque ambos modos se proyectarán sobre un único eje temporal.
from __future__ import annotations

from ada.processes.pi_web_api.catalog.definitions import DEFINITIONS, SOURCE
from ada.processes.pi_web_api.errors import PiWebApiCatalogError
from atlanticus.integrations.pi.contracts import PiCatalog, PiWebApiSource


def build_catalog() -> PiCatalog:
    if not isinstance(SOURCE, PiWebApiSource):
        raise PiWebApiCatalogError('PI Web API catalog source must be PiWebApiSource')
    if SOURCE.interpolation_seconds is None:
        raise PiWebApiCatalogError('PI Web API catalog must define interpolation_seconds')
    if not isinstance(DEFINITIONS, tuple):
        raise PiWebApiCatalogError('PI Web API catalog definitions must be a tuple')
    try:
        catalog = PiCatalog(source=SOURCE, definitions=DEFINITIONS)
    except (TypeError, ValueError) as error:
        raise PiWebApiCatalogError(str(error)) from error
    if not any(item.is_active for item in catalog.definitions):
        raise PiWebApiCatalogError('PI Web API catalog must contain active definitions')
    return catalog
