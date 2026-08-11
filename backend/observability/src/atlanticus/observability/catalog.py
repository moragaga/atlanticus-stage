"""Nombres estables reservados por el contrato público de eventos."""

from enum import StrEnum


class StandardEventName(StrEnum):
    """Catálogo inicial; los jobs pueden agregar nombres de dominio propios."""

    EXECUTION_STARTED = 'execution.started'
    EXECUTION_FINISHED = 'execution.finished'
    EXECUTION_FAILED = 'execution.failed'
    EXECUTION_TIMED_OUT = 'execution.timed_out'
    EXECUTION_CANCELLED = 'execution.cancelled'
    ITERATION_STARTED = 'iteration.started'
    ITERATION_FINISHED = 'iteration.finished'
    ITERATION_FAILED = 'iteration.failed'
    ITERATION_TIMED_OUT = 'iteration.timed_out'
    ITERATION_CANCELLED = 'iteration.cancelled'
    DEPENDENCY_STARTED = 'dependency.started'
    DEPENDENCY_FINISHED = 'dependency.finished'
    DEPENDENCY_SLOW = 'dependency.slow'
    DEPENDENCY_FAILED = 'dependency.failed'
    DATA_READ = 'data.read'
    DATA_WRITTEN = 'data.written'
    DATA_DOWNLOADED = 'data.downloaded'
    FILE_CREATED = 'file.created'
    RESOURCE_CHECKPOINT = 'resource.checkpoint'
    RESOURCE_SUMMARY = 'resource.summary'
    RESOURCE_PRESSURE_STARTED = 'resource.pressure.started'
    RESOURCE_PRESSURE_ESCALATED = 'resource.pressure.escalated'
    RESOURCE_PRESSURE_ONGOING = 'resource.pressure.ongoing'
    RESOURCE_PRESSURE_RECOVERED = 'resource.pressure.recovered'
    RESOURCE_PRESSURE_OPEN_AT_STOP = 'resource.pressure.open_at_monitor_stop'
    DIAGNOSTIC_LOG = 'diagnostic.log'
    CONCURRENCY_STARTED = 'concurrency.started'
    CONCURRENCY_FINISHED = 'concurrency.finished'
    CONCURRENCY_FAILED = 'concurrency.failed'
