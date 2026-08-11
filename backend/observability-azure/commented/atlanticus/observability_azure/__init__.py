"""Extensión Azure Monitor acotada para Atlanticus Observability."""

# La API pública expone composición, configuración y destinos. La proyección operacional se reutiliza
# directamente desde atlanticus-observability para evitar dos contratos equivalentes.
from atlanticus.observability_azure.bootstrap import (
    AzureLogBackendFactory,
    AzureObservabilityBootstrapError,
    AzureObservabilityExtension,
    build_azure_observability_extension,
)
from atlanticus.observability_azure.configuration import (
    APPLICATION_INSIGHTS_CONNECTION_STRING_VARIABLE,
    AZURE_OBSERVABILITY_MODE_VARIABLE,
    AZURE_OBSERVABILITY_PROFILE_VARIABLE,
    AzureObservabilityConfigurationError,
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

__version__ = '0.1.0'

__all__ = [
    'APPLICATION_INSIGHTS_CONNECTION_STRING_VARIABLE',
    'AZURE_OBSERVABILITY_MODE_VARIABLE',
    'AZURE_OBSERVABILITY_PROFILE_VARIABLE',
    'AzureLogBackend',
    'AzureLogBackendFactory',
    'AzureMonitorEventSink',
    'AzureMonitorTraceBridge',
    'AzureObservabilityBootstrapError',
    'AzureObservabilityConfigurationError',
    'AzureObservabilityExtension',
    'AzureObservabilityMode',
    'AzureObservabilityProfile',
    'AzureObservabilitySettings',
    'AzurePreviewSink',
    'AzurePreviewTraceBridge',
    'AzurePreviewWriter',
    'OpenTelemetryLogBackend',
    '__version__',
    'build_azure_observability_extension',
]
