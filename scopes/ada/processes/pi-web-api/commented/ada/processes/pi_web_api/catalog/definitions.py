# Esta es la única frontera declarativa que debe completar el desarrollador del proceso.
# SOURCE define la granularidad del eje PI y DEFINITIONS contiene las señales
# específicas de la aplicación.
# El resto del proceso consume este contrato sin requerir cambios en planner, job o composición.
from atlanticus.integrations.pi.contracts import PiTagDefinition, PiWebApiSource

SOURCE = PiWebApiSource(interpolation_seconds=10)
DEFINITIONS: tuple[PiTagDefinition, ...] = ()
