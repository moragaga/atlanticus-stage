"""Valores generales de estado para una operación."""

from __future__ import annotations

# ``StrEnum`` permite comparar y serializar los estados como texto sin perder el conjunto cerrado.
from enum import StrEnum


class OperationStatus(StrEnum):
    """Resultado serializable de una operación."""

    # Estos valores son generales y no describen estados específicos de KPI, alarmas o conexiones.
    SUCCESS = 'success'
    WARNING = 'warning'
    ERROR = 'error'
    SKIPPED = 'skipped'
