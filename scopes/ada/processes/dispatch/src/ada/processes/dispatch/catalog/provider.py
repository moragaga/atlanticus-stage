from __future__ import annotations

from ada.processes.dispatch.catalog.definitions import DEFINITIONS
from ada.processes.dispatch.errors import DispatchCatalogError
from ada.processes.dispatch.models import DispatchSourceDefinition


def build_catalog() -> tuple[DispatchSourceDefinition, ...]:
    if not isinstance(DEFINITIONS, tuple):
        raise DispatchCatalogError('Dispatch catalog definitions must be a tuple')
    if not DEFINITIONS:
        raise DispatchCatalogError('Dispatch catalog must contain at least one source')
    if not all(isinstance(definition, DispatchSourceDefinition) for definition in DEFINITIONS):
        raise DispatchCatalogError('Dispatch catalog contains an invalid source definition')
    source_keys = tuple(definition.source_key.lower() for definition in DEFINITIONS)
    if len(source_keys) != len(set(source_keys)):
        raise DispatchCatalogError('Dispatch catalog source keys must be unique')
    source_tables = tuple(definition.source_table.lower() for definition in DEFINITIONS)
    if len(source_tables) != len(set(source_tables)):
        raise DispatchCatalogError('Dispatch catalog source tables must be unique')
    enabled = tuple(definition for definition in DEFINITIONS if definition.enabled)
    if not enabled:
        raise DispatchCatalogError('Dispatch catalog must contain at least one enabled source')
    return enabled
