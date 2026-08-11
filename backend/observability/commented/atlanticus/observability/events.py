# Espejo pedagógico: conserva exactamente el contrato ejecutable del módulo events.py.
"""Atajos explícitos para cantidades de datos y archivos procesados."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from atlanticus.kernel import OperationStatus
from atlanticus.observability.models import EventAudience, EventCategory, ObservabilityEvent
from atlanticus.observability.state import emit_event


def emit_data_event(
    name: str,
    *,
    duration_ms: float | None = None,
    record_count: int | None = None,
    byte_count: int | None = None,
    file_count: int | None = None,
    metrics: Mapping[str, int | float] | None = None,
    attributes: Mapping[str, Any] | None = None,
    audience: EventAudience = EventAudience.LOCAL,
) -> bool:
    """Emite cantidades ya calculadas sin inspeccionar el dataset o archivo original."""

    values = dict(metrics or {})
    for key, value in (
        ('record_count', record_count),
        ('byte_count', byte_count),
        ('file_count', file_count),
    ):
        if value is not None:
            if value < 0:
                raise ValueError(f'{key} must be greater than or equal to zero')
            values[key] = value
    return emit_event(
        ObservabilityEvent(
            name=name,
            category=EventCategory.DATA,
            audience=audience,
            status=OperationStatus.SUCCESS,
            duration_ms=duration_ms,
            metrics=values,
            attributes=dict(attributes or {}),
        )
    )
