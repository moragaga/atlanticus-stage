# Declara vocabularios cerrados del dominio KPI.
# Las fuentes son tipadas y se amplían por código cuando Atlanticus incorpora una fuente nueva.
from __future__ import annotations

from enum import StrEnum


class KpiArea(StrEnum):
    GENERAL = 'general'
    MINA = 'mina'
    PLANTA = 'planta'


class KpiSource(StrEnum):
    PI_INTERPOLATED = 'pi.interpolated'
    PI_RECORDED = 'pi.recorded'

    DISPATCH_TIEMPOS_MLP = 'dispatch.tiempos_mlp'
    DISPATCH_STD_SHIFT_LOADS = 'dispatch.std_shift_loads'
    DISPATCH_STD_SHIFT_STATE = 'dispatch.std_shift_state'
    DISPATCH_STD_TRUCK = 'dispatch.std_truck'
    DISPATCH_STD_SHIFT_GRADE = 'dispatch.std_shift_grade'
    DISPATCH_STD_SHIFT_LOADS_2 = 'dispatch.std_shift_loads_2'
    DISPATCH_STD_SHIFT_DUMPS = 'dispatch.std_shift_dumps'

    BLOCKGRADE_MMS_BLOCKGRADE_DETAILS_BUCKET = 'blockgrade.mms_blockgrade_details_bucket'

    REMANENTES_EXTRAIBLES = 'remanentes.extraibles'
    REMANENTES_NO_EXTRAIBLES = 'remanentes.no_extraibles'
    REMANENTES_STOCKS = 'remanentes.stocks'

    FABRICA_PLANES_DAILY = 'fabrica.planes.daily'
    FABRICA_PLANES_WEEKLY = 'fabrica.planes.weekly'


class ShiftScope(StrEnum):
    CURRENT = 'current'
    PREVIOUS = 'previous'
    CURRENT_TURN = 'current_turn'
    PREVIOUS_TURN = 'previous_turn'
    CURRENT_WEEK = 'current_week'
    DAYS = 'days'


class KpiMode(StrEnum):
    LATEST = 'latest'
    LATEST_NUMBER = 'latest_number'
    STATUS = 'status'
    SUM_LATESTS_NUMBERS = 'sum_latests_numbers'
    MAX_LATESTS_NUMBERS = 'max_latests_numbers'
    CUSTOM = 'custom'
    CONSTANT = 'constant'


class KpiStatus(StrEnum):
    OK = 'ok'
    MISSING = 'missing'
    INVALID = 'invalid'
    ERROR = 'error'


class KpiValueKind(StrEnum):
    VALUE = 'value'
    JSON = 'json'
