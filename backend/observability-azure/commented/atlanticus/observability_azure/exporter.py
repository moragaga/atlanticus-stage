"""Sink OpenTelemetry explícito, sin autoinstrumentación ni métricas de runtime."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from typing import Any, Protocol

from atlanticus.kernel import DataSanitizer
from atlanticus.observability import (
    EventProjection,
    EventSeverity,
    EventSink,
    ObservabilityEvent,
    ObservabilitySettings,
)

_LOG_LEVELS = {
    EventSeverity.DEBUG: logging.DEBUG,
    EventSeverity.INFO: logging.INFO,
    EventSeverity.WARNING: logging.WARNING,
    EventSeverity.ERROR: logging.ERROR,
    EventSeverity.CRITICAL: logging.CRITICAL,
}


class AzureLogBackend(Protocol):
    """Backend pequeño que permite probar la proyección sin red."""

    def emit(self, payload: Mapping[str, Any], severity: EventSeverity) -> None:
        """Exporta un payload previamente sanitizado y filtrado."""

    def close(self) -> None:
        """Intenta sincronizar la cola dentro de su límite configurado."""


class AzureMonitorEventSink(EventSink):
    """Materializa una sola vez y delega el envío al backend OpenTelemetry."""

    def __init__(
        self,
        *,
        projection: EventProjection,
        backend: AzureLogBackend,
    ) -> None:
        if not isinstance(projection, EventProjection):
            raise TypeError('projection must be an EventProjection')
        _validate_backend(backend)
        self._projection = projection
        self._backend = backend
        self._closed = False

    def emit(
        self,
        event: ObservabilityEvent,
        settings: ObservabilitySettings,
        sanitizer: DataSanitizer,
    ) -> None:
        if self._closed:
            raise RuntimeError('Azure monitor event sink is closed')
        # La sanitización ocurre antes de proyectar y antes de cruzar la frontera del backend Azure.
        payload = event.to_dict(settings=settings, sanitizer=sanitizer)
        projected = self._projection.project(event, payload)
        if projected is not None:
            self._backend.emit(projected, event.severity)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._backend.close()


class OpenTelemetryLogBackend:
    """Configura únicamente logs Azure Monitor con almacenamiento offline deshabilitado."""

    def __init__(
        self,
        *,
        connection_string: str,
        application: str,
        service: str,
        flush_timeout_seconds: float,
    ) -> None:
        _require_text(connection_string, 'connection_string')
        _require_text(application, 'application')
        _require_text(service, 'service')
        _validate_timeout(flush_timeout_seconds)

        from azure.monitor.opentelemetry.exporter import AzureMonitorLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource

        self._flush_timeout_millis = int(flush_timeout_seconds * 1000)
        resource = Resource.create(
            {
                'service.namespace': application,
                'service.name': service,
            }
        )
        self._provider = LoggerProvider(resource=resource)
        exporter = AzureMonitorLogExporter(
            connection_string=connection_string,
            disable_offline_storage=True,
        )
        self._provider.add_log_record_processor(
            BatchLogRecordProcessor(
                exporter,
                export_timeout_millis=self._flush_timeout_millis,
            )
        )
        self._handler = LoggingHandler(logger_provider=self._provider)
        self._logger = logging.getLogger(f'atlanticus.azure.{application}.{service}.{id(self)}')
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._logger.addHandler(self._handler)
        self._closed = False

    def emit(self, payload: Mapping[str, Any], severity: EventSeverity) -> None:
        if self._closed:
            raise RuntimeError('Azure log backend is closed')
        if not isinstance(payload, Mapping):
            raise TypeError('payload must be a mapping')
        if not isinstance(severity, EventSeverity):
            raise TypeError('severity must be an EventSeverity')
        # El cuerpo contiene el JSON completo para parse_json(message). Las dimensiones duplicadas
        # son deliberadamente pocas para controlar cardinalidad y costo.
        self._logger.log(
            _LOG_LEVELS[severity],
            json.dumps(payload, ensure_ascii=False, separators=(',', ':'), sort_keys=True),
            extra={
                'atlanticus_event_name': payload.get('event', 'unknown'),
                'atlanticus_application': payload.get('application', 'unknown'),
                'atlanticus_environment': payload.get('environment', 'unknown'),
                'atlanticus_service': payload.get('service', 'unknown'),
                'atlanticus_run_id': payload.get('run_id', 'unknown'),
            },
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._logger.removeHandler(self._handler)
        # Shutdown se intenta incluso si el flush falla, evitando dejar recursos del SDK abiertos.
        try:
            self._provider.force_flush(timeout_millis=self._flush_timeout_millis)
        finally:
            self._provider.shutdown()


def _validate_backend(backend: Any) -> None:
    if not callable(getattr(backend, 'emit', None)) or not callable(
        getattr(backend, 'close', None)
    ):
        raise TypeError('backend must implement emit() and close()')


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a non-empty string')
    return value


def _validate_timeout(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError('flush_timeout_seconds must be an int or float')
    if not math.isfinite(value) or value <= 0:
        raise ValueError('flush_timeout_seconds must be greater than zero')
