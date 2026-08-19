# Estados de escritura que el proceso puede usar para diagnóstico e idempotencia.
from __future__ import annotations

from enum import StrEnum


class KpiEvaluationWriteStatus(StrEnum):
    CREATED = 'created'
    UNCHANGED = 'unchanged'
