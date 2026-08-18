from atlanticus.integrations.pi.contracts import NotPiiSource, PiTagDefinition

# Fuente NOTPII utilizada por este proceso.
SOURCE = NotPiiSource()

# El catálogo productivo se entrega deliberadamente vacío.
# Las definiciones concretas se incorporan únicamente en el entorno correspondiente.
DEFINITIONS: tuple[PiTagDefinition, ...] = ()
