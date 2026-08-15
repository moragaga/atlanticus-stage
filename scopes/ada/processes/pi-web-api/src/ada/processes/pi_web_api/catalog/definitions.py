from atlanticus.integrations.pi.contracts import PiTagDefinition, PiWebApiSource

SOURCE = PiWebApiSource(interpolation_seconds=10)

DEFINITIONS: tuple[PiTagDefinition, ...] = ()
