from enum import StrEnum


# Modos de extracción PI compartidos por las distintas fuentes.
class PiExtractionMode(StrEnum):
    INTERPOLATED = 'interpolated'
    RECORDED = 'recorded'


# Materializaciones que un tag puede solicitar al proceso que lo consume.
class PiMaterialization(StrEnum):
    LATEST = 'latest'
    DAILY = 'daily'
    MONTHLY = 'monthly'


# Tipos lógicos mínimos que Atlanticus necesita distinguir al normalizar PI.
class PiValueKind(StrEnum):
    NUMBER = 'number'
    TEXT = 'text'
