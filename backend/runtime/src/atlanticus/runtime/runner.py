"""Composición oficial de lease, observabilidad y ciclo de iteraciones."""

from __future__ import annotations

import math
import os
import re
import signal
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from types import FrameType
from typing import Any
from uuid import UUID, uuid4

from atlanticus.kernel import OperationStatus
from atlanticus.observability import (
    ErrorInfo,
    EventAudience,
    EventCategory,
    EventSeverity,
    ExecutionContext,
    ObservabilityEvent,
    ObservabilitySettings,
    close_observability,
    configure_volume_observability,
    emit_event,
    execution_scope,
    iteration_scope,
    trace_span,
)
from atlanticus.runtime._resource_monitor import ResourceMonitor
from atlanticus.runtime.configuration import RuntimeConfiguration
from atlanticus.runtime.context import JobRuntimeContext
from atlanticus.runtime.definition import JobDefinition
from atlanticus.runtime.errors import (
    AtlanticusRuntimeError,
    LeaseOwnershipLostError,
    RuntimeCancellationRequested,
)
from atlanticus.runtime.lease import ExecutionLease, LeaseAcquisition, RecoveredLease
from atlanticus.runtime.options import RuntimeOptions, parse_runtime_options

_AZURE_OBSERVABILITY_VARIABLES = (
    'ATLANTICUS_AZURE_OBSERVABILITY_MODE',
    'ATLANTICUS_AZURE_OBSERVABILITY_PROFILE',
    'APPLICATION_INSIGHTS_CONNECTION_STRING',
)


@dataclass(frozen=True, slots=True)
class RuntimeExecutionResult:
    """Resumen retornado al ejecutable cuando el cierre fue controlado."""

    run_id: str
    correlation_id: str
    status: OperationStatus
    iteration_count: int
    duration_seconds: float
    stop_reason: str

    def __post_init__(self) -> None:
        for name in ('run_id', 'correlation_id', 'stop_reason'):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f'{name} must be a string')
            if not value.strip():
                raise ValueError(f'{name} must not be empty')
        for name in ('run_id', 'correlation_id'):
            try:
                UUID(getattr(self, name))
            except ValueError as error:
                raise ValueError(f'{name} must be a UUID') from error
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', self.stop_reason):
            raise ValueError('stop_reason must use lower snake_case')
        if not isinstance(self.status, OperationStatus):
            raise TypeError('status must be an OperationStatus')
        if isinstance(self.iteration_count, bool) or not isinstance(self.iteration_count, int):
            raise TypeError('iteration_count must be an int')
        if self.iteration_count < 0:
            raise ValueError('iteration_count must be greater than or equal to zero')
        if isinstance(self.duration_seconds, bool) or not isinstance(
            self.duration_seconds, int | float
        ):
            raise TypeError('duration_seconds must be an int or float')
        if not math.isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ValueError('duration_seconds must be finite and greater than or equal to zero')


def execute_job(
    *,
    definition: JobDefinition,
    iteration: Callable[[JobRuntimeContext], Any],
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> RuntimeExecutionResult:
    """Ejecuta un job sin crear procesos o hilos de negocio implícitos."""

    if not isinstance(definition, JobDefinition):
        raise TypeError('definition must be a JobDefinition')
    if not callable(iteration):
        raise TypeError('iteration must be callable')
    if environ is not None and not isinstance(environ, Mapping):
        raise TypeError('environ must be a mapping')

    options = parse_runtime_options(definition=definition, argv=argv)
    source_environ = os.environ if environ is None else environ
    configuration = RuntimeConfiguration.from_sources(
        cli_environment=options.environment,
        environ=source_environ,
    )
    run_id = str(uuid4())
    correlation_id = str(uuid4())
    lease = ExecutionLease(
        volume_path=configuration.volume_path,
        application=configuration.application,
        service_name=definition.service_name,
        job_key=definition.job_key,
        module_name=definition.module_name,
        run_id=run_id,
        lease_timeout_seconds=definition.lease_timeout_seconds,
        renewal_seconds=definition.lease_renew_seconds,
        wait_seconds=definition.lease_wait_seconds,
        poll_seconds=definition.lease_poll_seconds,
    )
    acquisition = lease.acquire()
    observability_configured = False
    primary_error: BaseException | None = None
    try:
        settings = ObservabilitySettings.build(
            application=configuration.application,
            service=definition.service_name,
            module=definition.module_name,
            component='runtime',
            environment=configuration.environment,
            volume_path=configuration.volume_path,
        )
        _configure_runtime_observability(
            settings,
            environ=_project_azure_observability_environ(source_environ),
        )
        observability_configured = True
        _emit_lease_acquired(settings, acquisition, run_id, correlation_id)
        if acquisition.recovered is not None:
            _emit_recovered_timeout(settings, acquisition.recovered)

        context = JobRuntimeContext.create(
            definition=definition,
            configuration=configuration,
            run_id=run_id,
            correlation_id=correlation_id,
        )
        lease.start_renewal(on_lost=context.request_stop)
        try:
            with _cooperative_sigterm(context):
                return _run_iterations(
                    definition=definition,
                    options=options,
                    context=context,
                    iteration=iteration,
                    lease=lease,
                )
        finally:
            context.clear_memory()
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_error = _release_lease(lease)
        if cleanup_error is not None and observability_configured:
            _safe_emit_cleanup_failure(cleanup_error)
        close_error = _close_runtime_observability(observability_configured)
        if primary_error is None:
            if cleanup_error is not None:
                raise cleanup_error
            if close_error is not None:
                raise close_error


def _run_iterations(
    *,
    definition: JobDefinition,
    options: RuntimeOptions,
    context: JobRuntimeContext,
    iteration: Callable[[JobRuntimeContext], Any],
    lease: ExecutionLease,
) -> RuntimeExecutionResult:
    started = time.monotonic()
    durations: list[float] = []
    work_iterations = 0
    empty_iterations = 0
    stop_reason = 'completed'
    current_iteration = 0
    cancellation_reason: str | None = None

    with execution_scope(
        run_id=context.run_id,
        correlation_id=context.correlation_id,
    ) as execution_context:
        emit_event(
            ObservabilityEvent(
                name='execution.started',
                category=EventCategory.LIFECYCLE,
                audience=EventAudience.OPERATIONS,
                status='running',
                context=execution_context,
            )
        )
        resources = ResourceMonitor(interval_seconds=definition.resource_sample_seconds)
        resources_started = _start_resource_monitor(resources, execution_context)
        try:
            with trace_span('execution', attributes={'atlanticus.span_kind': 'execution'}):
                try:
                    while True:
                        lease.raise_if_unhealthy()
                        if context.should_stop:
                            if context.stop_reason is not None:
                                cancellation_reason = context.stop_reason
                            else:
                                stop_reason = 'safe_execution_window_elapsed'
                            break

                        iteration_number = len(durations) + 1
                        current_iteration = iteration_number
                        context._begin_iteration(iteration_number)
                        iteration_started = time.monotonic()
                        with iteration_scope(iteration_number) as iteration_context:
                            with trace_span(
                                'iteration',
                                attributes={'atlanticus.span_kind': 'iteration'},
                            ):
                                iteration(context)
                        lease.raise_if_unhealthy()
                        duration_seconds = time.monotonic() - iteration_started
                        durations.append(duration_seconds)
                        if resources_started:
                            resources.checkpoint()

                        if context.iteration_has_work:
                            work_iterations += 1
                            emit_event(
                                ObservabilityEvent(
                                    name='runtime.iteration.summary',
                                    category=EventCategory.ITERATION,
                                    audience=EventAudience.OPERATIONS,
                                    status=OperationStatus.SUCCESS,
                                    context=iteration_context,
                                    duration_ms=duration_seconds * 1000,
                                    attributes=context._iteration_facts(),
                                )
                            )
                        else:
                            empty_iterations += 1

                        if duration_seconds > definition.iteration_timeout_seconds:
                            emit_event(
                                ObservabilityEvent(
                                    name='iteration.timeout_warning',
                                    category=EventCategory.ITERATION,
                                    severity=EventSeverity.WARNING,
                                    status=OperationStatus.WARNING,
                                    context=iteration_context,
                                    duration_ms=duration_seconds * 1000,
                                    message='Iteration exceeded its configured timeout',
                                )
                            )

                        if context.should_stop:
                            if context.stop_reason is not None:
                                cancellation_reason = context.stop_reason
                            else:
                                stop_reason = 'safe_execution_window_elapsed'
                            break
                        if options.run_once:
                            stop_reason = 'run_once'
                            break

                        average_duration = sum(durations) / len(durations)
                        sleep_seconds = max(0.0, definition.sleep_seconds - duration_seconds)
                        required_for_next = sleep_seconds + average_duration
                        if context.safe_remaining_seconds <= required_for_next:
                            stop_reason = 'insufficient_remaining_time'
                            break
                        if sleep_seconds > 0 and not context.wait(sleep_seconds):
                            if context.stop_reason is not None:
                                cancellation_reason = context.stop_reason
                            else:
                                stop_reason = 'safe_execution_window_elapsed'
                            break
                except RuntimeCancellationRequested as cancellation:
                    lease.raise_if_unhealthy()
                    cancellation_reason = context.stop_reason or cancellation.reason
                except KeyboardInterrupt:
                    context.request_stop('interrupted')
                    cancellation_reason = 'interrupted'
                lease.raise_if_unhealthy()
            _stop_resource_monitor(resources, resources_started, execution_context)
            resources_started = False
            lease.stop_renewal()
            lease.raise_if_unhealthy()
        except BaseException as error:
            _stop_resource_monitor(resources, resources_started, execution_context)
            duration_seconds = time.monotonic() - started
            emit_event(
                ObservabilityEvent(
                    name='execution.failed',
                    category=EventCategory.LIFECYCLE,
                    audience=EventAudience.OPERATIONS,
                    severity=EventSeverity.ERROR,
                    status=OperationStatus.ERROR,
                    context=execution_context,
                    duration_ms=duration_seconds * 1000,
                    metrics=_execution_metrics(
                        durations=durations,
                        work_iterations=work_iterations,
                        empty_iterations=empty_iterations,
                        resources=resources,
                    ),
                    attributes={
                        'stop_reason': 'error',
                        **(
                            {'failed_iteration': current_iteration}
                            if current_iteration > len(durations)
                            else {}
                        ),
                        **context._execution_facts(),
                    },
                    error=ErrorInfo.from_exception(error),
                )
            )
            raise

        _stop_resource_monitor(resources, resources_started, execution_context)
        duration_seconds = time.monotonic() - started
        if cancellation_reason is not None:
            emit_event(
                ObservabilityEvent(
                    name='execution.cancelled',
                    category=EventCategory.LIFECYCLE,
                    audience=EventAudience.OPERATIONS,
                    status=OperationStatus.WARNING,
                    context=execution_context,
                    duration_ms=duration_seconds * 1000,
                    metrics=_execution_metrics(
                        durations=durations,
                        work_iterations=work_iterations,
                        empty_iterations=empty_iterations,
                        resources=resources,
                    ),
                    attributes={
                        'stop_reason': cancellation_reason,
                        **(
                            {'cancelled_iteration': current_iteration}
                            if current_iteration > len(durations)
                            else {}
                        ),
                        **context._execution_facts(),
                    },
                    message='Execution stopped by a cooperative cancellation request',
                )
            )
            return RuntimeExecutionResult(
                run_id=context.run_id,
                correlation_id=context.correlation_id,
                status=OperationStatus.WARNING,
                iteration_count=len(durations),
                duration_seconds=round(duration_seconds, 6),
                stop_reason=cancellation_reason,
            )

        emit_event(
            ObservabilityEvent(
                name='runtime.execution.summary',
                category=EventCategory.LIFECYCLE,
                audience=EventAudience.OPERATIONS,
                status=OperationStatus.SUCCESS,
                context=execution_context,
                duration_ms=duration_seconds * 1000,
                metrics=_execution_metrics(
                    durations=durations,
                    work_iterations=work_iterations,
                    empty_iterations=empty_iterations,
                    resources=resources,
                ),
                attributes={
                    'stop_reason': stop_reason,
                    **context._execution_facts(),
                },
            )
        )

    return RuntimeExecutionResult(
        run_id=context.run_id,
        correlation_id=context.correlation_id,
        status=OperationStatus.SUCCESS,
        iteration_count=len(durations),
        duration_seconds=round(time.monotonic() - started, 6),
        stop_reason=stop_reason,
    )


def _execution_metrics(
    *,
    durations: list[float],
    work_iterations: int,
    empty_iterations: int,
    resources: ResourceMonitor,
) -> dict[str, int | float]:
    metrics: dict[str, int | float] = {
        'iterations': len(durations),
        'work_iterations': work_iterations,
        'empty_iterations': empty_iterations,
    }
    if resources.pressure_event_count > 0:
        metrics['resource_pressure_events'] = resources.pressure_event_count
    metrics.update(resources.statistics.operational_metrics())
    return metrics


def _configure_runtime_observability(
    settings: ObservabilitySettings,
    *,
    environ: Mapping[str, str],
) -> None:
    include_console = settings.environment.is_local
    azure_mode = environ.get('ATLANTICUS_AZURE_OBSERVABILITY_MODE', 'off').strip().lower()
    if azure_mode == 'off':
        configure_volume_observability(settings=settings, include_console=include_console)
        return

    try:
        from atlanticus.observability_azure import build_azure_observability_extension

        extension = build_azure_observability_extension(
            observability_settings=settings,
            environ=environ,
            volume_path=settings.volume_path,
        )
    except Exception as error:
        configure_volume_observability(settings=settings, include_console=include_console)
        emit_event(
            ObservabilityEvent(
                name='observability.azure.bootstrap.failed',
                category=EventCategory.DEPENDENCY,
                audience=EventAudience.OPERATIONS,
                severity=EventSeverity.WARNING,
                status=OperationStatus.WARNING,
                message='Azure observability could not be configured; local trace remains active',
                error=ErrorInfo.from_exception(error),
            )
        )
        return

    configure_volume_observability(
        settings=settings,
        include_console=include_console,
        additional_sinks=(extension.sink,) if extension.enabled else (),
        trace_bridge=extension.trace_bridge,
    )


def _project_azure_observability_environ(
    environ: Mapping[str, str],
) -> dict[str, str]:
    projected: dict[str, str] = {}
    for name in _AZURE_OBSERVABILITY_VARIABLES:
        if name not in environ:
            continue
        value = environ[name]
        if not isinstance(value, str):
            raise TypeError(f'{name} must be a string')
        projected[name] = value
    return projected


def _emit_lease_acquired(
    settings: ObservabilitySettings,
    acquisition: LeaseAcquisition,
    run_id: str,
    correlation_id: str,
) -> None:
    emit_event(
        ObservabilityEvent(
            name='runtime.lease.acquired',
            category=EventCategory.CONCURRENCY,
            context=ExecutionContext(
                **settings.base_context().to_dict(),
                run_id=run_id,
                correlation_id=correlation_id,
            ),
            metrics={'waited_seconds': acquisition.waited_seconds},
            attributes={'recovered_expired_lease': acquisition.recovered is not None},
        )
    )


def _emit_recovered_timeout(
    settings: ObservabilitySettings,
    recovered: RecoveredLease,
) -> None:
    emit_event(
        ObservabilityEvent(
            name='execution.timed_out',
            category=EventCategory.LIFECYCLE,
            severity=EventSeverity.ERROR,
            status=OperationStatus.ERROR,
            context=ExecutionContext(
                application=settings.application,
                service=settings.service,
                module=settings.module,
                component=settings.component,
                environment=str(settings.environment),
                instance_id=recovered.instance_id,
                process_id=recovered.process_id,
                run_id=recovered.run_id,
            ),
            message='Previous execution lease expired before a controlled shutdown',
            attributes={
                'acquired_at_utc': recovered.acquired_at_utc,
                'expires_at_utc': recovered.expires_at_utc,
                'detected_by': 'runtime_lease_recovery',
            },
        )
    )


def _start_resource_monitor(
    resources: ResourceMonitor,
    context: ExecutionContext,
) -> bool:
    try:
        resources.start()
    except Exception as error:
        _emit_resource_monitor_warning(
            name='resource.monitor.start_failed',
            message='Resource monitoring could not start; job execution continues',
            error=error,
            context=context,
        )
        return False
    return True


def _stop_resource_monitor(
    resources: ResourceMonitor,
    started: bool,
    context: ExecutionContext,
) -> None:
    if not started:
        return
    try:
        resources.stop()
    except Exception as error:
        _emit_resource_monitor_warning(
            name='resource.monitor.stop_failed',
            message='Resource monitoring could not stop cleanly; job execution continues',
            error=error,
            context=context,
        )


def _emit_resource_monitor_warning(
    *,
    name: str,
    message: str,
    error: Exception,
    context: ExecutionContext,
) -> None:
    try:
        emit_event(
            ObservabilityEvent(
                name=name,
                category=EventCategory.RESOURCE,
                audience=EventAudience.OPERATIONS,
                severity=EventSeverity.WARNING,
                status=OperationStatus.WARNING,
                context=context,
                message=message,
                error=ErrorInfo.from_exception(error),
            )
        )
    except Exception:
        return


@contextmanager
def _cooperative_sigterm(context: JobRuntimeContext) -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    previous_handler = signal.getsignal(signal.SIGTERM)

    def request_stop(signum: int, frame: FrameType | None) -> None:
        context.request_stop('sigterm')

    signal.signal(signal.SIGTERM, request_stop)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


def _release_lease(lease: ExecutionLease) -> AtlanticusRuntimeError | None:
    try:
        released = lease.release()
    except Exception:
        return LeaseOwnershipLostError('Lease release failed')
    if lease.failure is not None:
        return lease.failure
    if released:
        return None
    return LeaseOwnershipLostError('Lease could not be released safely')


def _safe_emit_cleanup_failure(error: AtlanticusRuntimeError) -> None:
    try:
        emit_event(
            ObservabilityEvent(
                name='runtime.lease.release_failed',
                category=EventCategory.CONCURRENCY,
                audience=EventAudience.OPERATIONS,
                severity=EventSeverity.WARNING,
                status=OperationStatus.WARNING,
                message='Runtime lease could not be released safely',
                error=ErrorInfo.from_exception(error),
            )
        )
    except Exception:
        return


def _close_runtime_observability(configured: bool) -> AtlanticusRuntimeError | None:
    if not configured:
        return None
    try:
        close_observability()
    except Exception:
        return AtlanticusRuntimeError('Observability shutdown failed')
    return None
