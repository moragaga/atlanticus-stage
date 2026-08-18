from __future__ import annotations

from ada.processes.blockgrade.catalog.definitions import DEFINITIONS
from ada.processes.blockgrade.errors import BlockgradeCatalogError
from atlanticus.data_producers.sql import SqlSourceDefinition


def build_catalog() -> tuple[SqlSourceDefinition, ...]:
    if not isinstance(DEFINITIONS, tuple):
        raise BlockgradeCatalogError('Blockgrade catalog definitions must be a tuple')
    if not DEFINITIONS:
        raise BlockgradeCatalogError('Blockgrade catalog must contain at least one source')
    if not all(isinstance(definition, SqlSourceDefinition) for definition in DEFINITIONS):
        raise BlockgradeCatalogError('Blockgrade catalog contains an invalid source definition')
    source_keys = tuple(definition.source_key.lower() for definition in DEFINITIONS)
    if len(source_keys) != len(set(source_keys)):
        raise BlockgradeCatalogError('Blockgrade catalog source keys must be unique')
    source_tables = tuple(definition.source_table.lower() for definition in DEFINITIONS)
    if len(source_tables) != len(set(source_tables)):
        raise BlockgradeCatalogError('Blockgrade catalog source tables must be unique')
    enabled = tuple(definition for definition in DEFINITIONS if definition.enabled)
    if not enabled:
        raise BlockgradeCatalogError('Blockgrade catalog must contain at least one enabled source')
    return enabled
