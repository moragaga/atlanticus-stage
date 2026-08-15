# Catálogo concreto del process PI Web API.
# El motor permanece genérico; las definiciones de tags se agregan aquí de forma explícita.
# Mantener este archivo sin credenciales ni lógica de adquisición/materialización.

from atlanticus.integrations.pi.contracts import PiTagDefinition, PiWebApiSource

# Intervalo transversal usado por el planner para INTERPOLATED y por las ventanas de consulta.
SOURCE = PiWebApiSource(interpolation_seconds=10)

# Zona de desarrollo: agregar PiTagDefinition productivas antes de ejecutar el process.
DEFINITIONS: tuple[PiTagDefinition, ...] = ()
