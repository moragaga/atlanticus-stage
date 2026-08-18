# Reúne únicamente las fuentes concretas habilitadas para esta implementación ADA.
from ada.processes.dispatch.catalog.tables.std_shift_dumps import DEFINITION as STD_SHIFT_DUMPS
from ada.processes.dispatch.catalog.tables.std_shift_grade import DEFINITION as STD_SHIFT_GRADE
from ada.processes.dispatch.catalog.tables.std_shift_loads import DEFINITION as STD_SHIFT_LOADS
from ada.processes.dispatch.catalog.tables.std_shift_loads_2 import (
    DEFINITION as STD_SHIFT_LOADS_2,
)
from ada.processes.dispatch.catalog.tables.std_shift_state import DEFINITION as STD_SHIFT_STATE
from ada.processes.dispatch.catalog.tables.std_truck import DEFINITION as STD_TRUCK
from ada.processes.dispatch.catalog.tables.tiempos_mlp import DEFINITION as TIEMPOS_MLP

DEFINITIONS = (
    TIEMPOS_MLP,
    STD_SHIFT_STATE,
    STD_SHIFT_LOADS,
    STD_SHIFT_DUMPS,
    STD_SHIFT_GRADE,
    STD_SHIFT_LOADS_2,
    STD_TRUCK,
)
