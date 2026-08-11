"""Preview diario de la proyección exacta que se enviaría a Azure."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from atlanticus.kernel import DataSanitizer
from atlanticus.observability import (
    AtomicJsonlWriter,
    EventProjection,
    EventSeverity,
    EventSink,
    ObservabilityEvent,
    ObservabilitySettings,
    resolve_observability_day_directory,
)


class AzurePreviewWriter:
    """Comparte una única escritura atómica entre logs y spans de preview."""

    def __init__(self, volume_path: str | Path) -> None:
        if not isinstance(volume_path, str | Path):
            raise TypeError('volume_path must be a string or Path')
        if isinstance(volume_path, str) and not volume_path.strip():
            raise ValueError('volume_path must not be empty')
        self._volume_path = Path(volume_path)
        self._writer = AtomicJsonlWriter()

    def append(
        self,
        payload: Mapping[str, Any],
        *,
        settings: ObservabilitySettings,
        event_day: date,
        durable: bool = False,
        file_name: str = 'azure-preview.jsonl',
    ) -> None:
        if not isinstance(payload, Mapping):
            raise TypeError('payload must be a mapping')
        if not isinstance(settings, ObservabilitySettings):
            raise TypeError('settings must be an ObservabilitySettings')
        if not isinstance(event_day, date):
            raise TypeError('event_day must be a date')
        if not isinstance(durable, bool):
            raise TypeError('durable must be a bool')
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValueError('file_name must be a non-empty string')
        # Preview usa la ruta diaria ya definida por observability, pero archivos propios de Azure.
        directory = resolve_observability_day_directory(
            self._volume_path,
            application=settings.application,
            service=settings.service,
            event_day=event_day,
        )
        self._writer.append(
            directory / file_name,
            dict(payload),
            durable=durable,
        )


class AzurePreviewSink(EventSink):
    """Persiste sólo los eventos que la proyección Azure aceptaría."""

    def __init__(
        self,
        *,
        writer: AzurePreviewWriter,
        projection: EventProjection,
    ) -> None:
        if not isinstance(writer, AzurePreviewWriter):
            raise TypeError('writer must be an AzurePreviewWriter')
        if not isinstance(projection, EventProjection):
            raise TypeError('projection must be an EventProjection')
        self._writer = writer
        self._projection = projection

    def emit(
        self,
        event: ObservabilityEvent,
        settings: ObservabilitySettings,
        sanitizer: DataSanitizer,
    ) -> None:
        # Se ejecuta la misma proyección que en export para que preview sea una simulación fiel.
        payload = event.to_dict(settings=settings, sanitizer=sanitizer)
        projected = self._projection.project(event, payload)
        if projected is None:
            return
        self._writer.append(
            projected,
            settings=settings,
            event_day=event.occurred_at_utc.date(),
            durable=event.severity
            in {EventSeverity.WARNING, EventSeverity.ERROR, EventSeverity.CRITICAL},
        )
