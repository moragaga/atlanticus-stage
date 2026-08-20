"""Composición conveniente para la traza local sin configuración implícita."""

from __future__ import annotations

from pathlib import Path

from atlanticus.kernel import DataSanitizer
from atlanticus.observability.models import ObservabilitySettings
from atlanticus.observability.operational import OperationalEventProjection
from atlanticus.observability.persistence import DailyTraceSink
from atlanticus.observability.sinks import (
    CompositeEventSink,
    ConsoleTextSink,
    EventSink,
    NoopEventSink,
)
from atlanticus.observability.state import Observability, configure_observability
from atlanticus.observability.tracing import TraceBridge


def configure_volume_observability(
    *,
    settings: ObservabilitySettings,
    volume_path: str | Path | None = None,
    include_console: bool = True,
    additional_sinks: tuple[EventSink, ...] = (),
    sanitizer: DataSanitizer | None = None,
    trace_bridge: TraceBridge | None = None,
) -> Observability:
    """Activa la traza persistente dentro del volumen entregado por el runtime."""

    # El bootstrap sólo compone objetos ya resueltos; no lee ambiente ni decide políticas de Azure.
    if not isinstance(settings, ObservabilitySettings):
        raise TypeError('settings must be ObservabilitySettings')
    if volume_path is not None and not isinstance(volume_path, str | Path):
        raise TypeError('volume_path must be a string or Path')
    if not isinstance(include_console, bool):
        raise TypeError('include_console must be a bool')
    if not isinstance(additional_sinks, tuple):
        raise TypeError('additional_sinks must be a tuple')
    if any(not isinstance(sink, EventSink) for sink in additional_sinks):
        raise TypeError('additional_sinks must contain only EventSink instances')
    resolved_volume = Path(volume_path) if volume_path is not None else settings.volume_path
    operational_projection = OperationalEventProjection()
    # Archivo, consola y extensiones externas se componen de forma independiente.
    sinks: list[EventSink] = []
    # Desactivar archivos no desactiva consola ni sinks adicionales como Azure.
    if settings.file_logs_enabled:
        if resolved_volume is None:
            raise ValueError(
                'volume_path or settings.volume_path is required when file logs are enabled'
            )
        sinks.append(DailyTraceSink(resolved_volume, projection=operational_projection))
    if include_console:
        sinks.append(ConsoleTextSink(projection=operational_projection))
    sinks.extend(additional_sinks)
    # Una composición explícitamente vacía sigue siendo válida y no genera archivos.
    sink = CompositeEventSink(sinks) if sinks else NoopEventSink()
    return configure_observability(
        settings=settings,
        sink=sink,
        sanitizer=sanitizer,
        trace_bridge=trace_bridge,
    )
