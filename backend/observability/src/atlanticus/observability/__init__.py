"""Contratos y trazas neutrales para procesos Atlanticus."""

from atlanticus.observability.bootstrap import configure_volume_observability
from atlanticus.observability.catalog import StandardEventName
from atlanticus.observability.context import (
    context_scope,
    copied_execution_context,
    execution_scope,
    get_execution_context,
    iteration_scope,
    operation_scope,
    set_execution_context,
    with_current_context,
)
from atlanticus.observability.events import emit_data_event
from atlanticus.observability.guard import runtime_guard
from atlanticus.observability.lifecycle import trace_execution, trace_iteration
from atlanticus.observability.logger import ObservabilityLogger, get_observability_logger
from atlanticus.observability.models import (
    ATLANTICUS_OBSERVABILITY_FILE_LOGS_ENABLED_VARIABLE,
    ErrorInfo,
    EventAudience,
    EventCategory,
    EventSeverity,
    ExecutionContext,
    ObservabilityEvent,
    ObservabilitySettings,
    ResultSummary,
)
from atlanticus.observability.operational import OperationalEventProjection
from atlanticus.observability.persistence import (
    AtomicJsonlWriter,
    AtomicJsonWriter,
    DailyTraceSink,
    resolve_observability_day_directory,
    resolve_observability_root,
)
from atlanticus.observability.sinks import (
    CompositeEventSink,
    ConsoleJsonSink,
    ConsoleTextSink,
    EventProjection,
    EventSink,
    FilteredEventProjection,
    FullEventProjection,
    MemoryEventSink,
    NoopEventSink,
)
from atlanticus.observability.state import (
    Observability,
    close_observability,
    configure_observability,
    emit_event,
    get_observability,
    trace_span,
)
from atlanticus.observability.tracing import (
    NoopTraceBridge,
    SpanError,
    SpanHandle,
    TraceBridge,
)

__version__ = '0.5.0'

__all__ = [
    'AtomicJsonWriter',
    'AtomicJsonlWriter',
    'CompositeEventSink',
    'ConsoleJsonSink',
    'ConsoleTextSink',
    'DailyTraceSink',
    'ErrorInfo',
    'EventAudience',
    'EventCategory',
    'EventProjection',
    'EventSeverity',
    'EventSink',
    'ExecutionContext',
    'FilteredEventProjection',
    'FullEventProjection',
    'MemoryEventSink',
    'NoopEventSink',
    'NoopTraceBridge',
    'ATLANTICUS_OBSERVABILITY_FILE_LOGS_ENABLED_VARIABLE',
    'Observability',
    'ObservabilityEvent',
    'ObservabilityLogger',
    'OperationalEventProjection',
    'ObservabilitySettings',
    'ResultSummary',
    'SpanError',
    'SpanHandle',
    'StandardEventName',
    'TraceBridge',
    '__version__',
    'close_observability',
    'configure_observability',
    'configure_volume_observability',
    'context_scope',
    'copied_execution_context',
    'emit_data_event',
    'emit_event',
    'execution_scope',
    'get_execution_context',
    'get_observability',
    'get_observability_logger',
    'iteration_scope',
    'operation_scope',
    'resolve_observability_root',
    'resolve_observability_day_directory',
    'runtime_guard',
    'set_execution_context',
    'trace_execution',
    'trace_iteration',
    'trace_span',
    'with_current_context',
]
