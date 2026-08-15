# Espejo comentado del proceso NOTPII; la lógica coincide con producción.
from __future__ import annotations

from ada.processes.notpii.catalog.definitions import DEFINITIONS, SOURCE
from ada.processes.notpii.errors import NotPiiCatalogError
from atlanticus.integrations.pi.contracts import NotPiiSource, PiCatalog, PiExtractionMode

_MODE_ORDER = (PiExtractionMode.INTERPOLATED, PiExtractionMode.RECORDED)


def build_catalog() -> PiCatalog:
    if not isinstance(SOURCE, NotPiiSource):
        raise NotPiiCatalogError('NOT PII catalog source must be NotPiiSource')
    if not isinstance(DEFINITIONS, tuple):
        raise NotPiiCatalogError('NOT PII catalog definitions must be a tuple')
    try:
        catalog = PiCatalog(source=SOURCE, definitions=DEFINITIONS)
    except (TypeError, ValueError) as error:
        raise NotPiiCatalogError(str(error)) from error
    if not any(item.is_active for item in catalog.definitions):
        raise NotPiiCatalogError('NOT PII catalog must contain active definitions')
    return catalog


def active_extraction_modes(catalog: PiCatalog) -> tuple[PiExtractionMode, ...]:
    if not isinstance(catalog, PiCatalog):
        raise TypeError('catalog must be a PiCatalog')
    modes = tuple(
        mode
        for mode in _MODE_ORDER
        if any(
            item.is_active and item.extraction_mode is mode
            for item in catalog.definitions
        )
    )
    if not modes:
        raise NotPiiCatalogError('NOT PII catalog must contain active definitions')
    return modes
