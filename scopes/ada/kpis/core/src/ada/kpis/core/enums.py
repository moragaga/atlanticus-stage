from __future__ import annotations

from enum import StrEnum


class KpiArea(StrEnum):
    GENERAL = 'general'
    MINA = 'mina'
    PLANTA = 'planta'


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
    ERROR = 'error'


class KpiValueKind(StrEnum):
    VALUE = 'value'
    JSON = 'json'
