"""Construcción explícita de sinks y tracing según modo y perfil."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlanticus.observability import (
    EventSink,
    NoopEventSink,
    NoopTraceBridge,
    ObservabilitySettings,
    OperationalEventProjection,
)
from atlanticus.observability.tracing import TraceBridge
from atlanticus.observability_azure.configuration import (
    AzureObservabilityMode,
    AzureObservabilityProfile,
    AzureObservabilitySettings,
)
from atlanticus.observability_azure.exporter import (
    AzureLogBackend,
    AzureMonitorEventSink,
    OpenTelemetryLogBackend,
)
from atlanticus.observability_azure.preview import AzurePreviewSink, AzurePreviewWriter
from atlanticus.observability_azure.tracing import (
    AzureMonitorTraceBridge,
    AzurePreviewTraceBridge,
)

AzureLogBackendFactory = Callable[
    [AzureObservabilitySettings, ObservabilitySettings], AzureLogBackend
]


class AzureObservabilityBootstrapError(RuntimeError):
    """Indica un fallo seguro al construir componentes del proveedor."""


@dataclass(frozen=True, slots=True)
class AzureObservabilityExtension:
    """Componentes que se agregan a la composición neutral de observabilidad."""

    settings: AzureObservabilitySettings
    sink: EventSink
    trace_bridge: TraceBridge

    def __post_init__(self) -> None:
        if not isinstance(self.settings, AzureObservabilitySettings):
            raise TypeError('settings must be an AzureObservabilitySettings')
        if not isinstance(self.sink, EventSink):
            raise TypeError('sink must be an EventSink')
        _validate_trace_bridge(self.trace_bridge)

    @property
    def enabled(self) -> bool:
        return self.settings.mode is not AzureObservabilityMode.OFF


def build_azure_observability_extension(
    *,
    observability_settings: ObservabilitySettings,
    environ: Mapping[str, str] | None = None,
    volume_path: str | Path | None = None,
    backend_factory: AzureLogBackendFactory | None = None,
) -> AzureObservabilityExtension:
    """Construye la extensión sin modificar el estado global de observabilidad."""

    if not isinstance(observability_settings, ObservabilitySettings):
        raise TypeError('observability_settings must be an ObservabilitySettings')
    if environ is not None and not isinstance(environ, Mapping):
        raise TypeError('environ must be a mapping or None')
    if volume_path is not None and not isinstance(volume_path, str | Path):
        raise TypeError('volume_path must be a string, Path or None')
    if backend_factory is not None and not callable(backend_factory):
        raise TypeError('backend_factory must be callable or None')

    azure_settings = AzureObservabilitySettings.from_sources(environ=environ)
    if azure_settings.mode is AzureObservabilityMode.OFF:
        return AzureObservabilityExtension(
            settings=azure_settings,
            sink=NoopEventSink(),
            trace_bridge=NoopTraceBridge(),
        )

    projection = OperationalEventProjection()
    if azure_settings.mode is AzureObservabilityMode.PREVIEW:
        resolved_volume = (
            Path(volume_path) if volume_path is not None else observability_settings.volume_path
        )
        if resolved_volume is None:
            raise AzureObservabilityBootstrapError('volume_path is required in preview mode')
        try:
            writer = AzurePreviewWriter(resolved_volume)
            bridge: TraceBridge = NoopTraceBridge()
            if azure_settings.profile is AzureObservabilityProfile.DIAGNOSTIC:
                bridge = AzurePreviewTraceBridge(
                    settings=observability_settings,
                    writer=writer,
                )
            return AzureObservabilityExtension(
                settings=azure_settings,
                sink=AzurePreviewSink(writer=writer, projection=projection),
                trace_bridge=bridge,
            )
        except AzureObservabilityBootstrapError:
            raise
        except Exception as error:
            raise AzureObservabilityBootstrapError(
                'Azure observability preview bootstrap failed'
            ) from error

    factory = backend_factory or _default_backend_factory
    backend = None
    bridge = None
    try:
        backend = factory(azure_settings, observability_settings)
        _validate_backend(backend)
        trace_bridge: TraceBridge = NoopTraceBridge()
        if azure_settings.profile is AzureObservabilityProfile.DIAGNOSTIC:
            assert azure_settings.connection_string is not None
            bridge = AzureMonitorTraceBridge(
                connection_string=azure_settings.connection_string,
                application=observability_settings.application,
                service=observability_settings.service,
                flush_timeout_seconds=azure_settings.flush_timeout_seconds,
            )
            trace_bridge = bridge
        return AzureObservabilityExtension(
            settings=azure_settings,
            sink=AzureMonitorEventSink(projection=projection, backend=backend),
            trace_bridge=trace_bridge,
        )
    except Exception as error:
        _safe_close(bridge)
        _safe_close(backend)
        raise AzureObservabilityBootstrapError('Azure observability bootstrap failed') from error


def _default_backend_factory(
    azure_settings: AzureObservabilitySettings,
    observability_settings: ObservabilitySettings,
) -> AzureLogBackend:
    assert azure_settings.connection_string is not None
    return OpenTelemetryLogBackend(
        connection_string=azure_settings.connection_string,
        application=observability_settings.application,
        service=observability_settings.service,
        flush_timeout_seconds=azure_settings.flush_timeout_seconds,
    )


def _validate_backend(backend: Any) -> None:
    if not callable(getattr(backend, 'emit', None)) or not callable(
        getattr(backend, 'close', None)
    ):
        raise TypeError('backend must implement emit() and close()')


def _validate_trace_bridge(trace_bridge: Any) -> None:
    if not callable(getattr(trace_bridge, 'start_span', None)) or not callable(
        getattr(trace_bridge, 'close', None)
    ):
        raise TypeError('trace_bridge must implement start_span() and close()')


def _safe_close(component: Any) -> None:
    close = getattr(component, 'close', None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        return
