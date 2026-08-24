# Expone únicamente el contrato público estable de la integración Runtime-Persistence.
# La lógica funcional del Alarm Engine se incorporará en un incremento posterior sin alterar esta frontera.
from ada.processes.alarms_runtime.composition import (
    AlarmRuntimePersistenceComposition,
    build_persistence_composition,
)
from ada.processes.alarms_runtime.durability import AlarmRuntimeDurability

__version__ = '0.1.0'

__all__ = [
    'AlarmRuntimeDurability',
    'AlarmRuntimePersistenceComposition',
    '__version__',
    'build_persistence_composition',
]
