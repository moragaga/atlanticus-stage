"""Valores generales de estado para una operación."""

from __future__ import annotations

from enum import StrEnum


class OperationStatus(StrEnum):
    """Resultado serializable de una operación."""

    SUCCESS = 'success'
    WARNING = 'warning'
    ERROR = 'error'
    SKIPPED = 'skipped'
