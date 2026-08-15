# Espejo comentado del proceso NOTPII: composición, batch, materialización, estado y settlement.
# Este archivo conserva exactamente el comportamiento productivo y agrega solo contexto.
from atlanticus.integrations.pi.contracts import NotPiiSource, PiTagDefinition

SOURCE = NotPiiSource()

DEFINITIONS: tuple[PiTagDefinition, ...] = ()
