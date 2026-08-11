"""Persistencia operacional compacta para jobs Atlanticus."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from atlanticus.kernel import DataSanitizer
from atlanticus.observability.models import EventSeverity, ObservabilityEvent, ObservabilitySettings
from atlanticus.observability.operational import OperationalEventProjection
from atlanticus.observability.sinks import EventProjection, EventSink

_DAY_DIRECTORY_PATTERN = re.compile(r'^day=(\d{4}-\d{2}-\d{2})$')
_EXECUTION_RECORD_EVENTS = frozenset(
    {'runtime.execution.summary', 'execution.failed', 'execution.timed_out', 'execution.cancelled'}
)
_ITERATION_RECORD_EVENTS = frozenset({'runtime.iteration.summary'})
_LATEST_EVENTS = _EXECUTION_RECORD_EVENTS | {'execution.started'}
_ISSUE_SEVERITIES = frozenset({EventSeverity.WARNING, EventSeverity.ERROR, EventSeverity.CRITICAL})


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r'[^a-zA-Z0-9_.-]+', '_', value.strip())
    return cleaned[:120] or 'unknown'


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    ).encode('utf-8')


def resolve_observability_root(
    volume_path: str | Path,
    *,
    application: str,
) -> Path:
    if not isinstance(volume_path, str | Path):
        raise TypeError('volume_path must be a string or Path')
    if not isinstance(application, str):
        raise TypeError('application must be a string')
    raw_path = str(volume_path).strip()
    if not raw_path:
        raise ValueError('volume_path must not be empty')
    if not application.strip():
        raise ValueError('application must not be empty')
    return Path(raw_path) / _safe_segment(application) / 'logs'


def resolve_observability_day_directory(
    volume_path: str | Path,
    *,
    application: str,
    service: str,
    event_day: date,
) -> Path:
    if not isinstance(service, str):
        raise TypeError('service must be a string')
    if not service.strip():
        raise ValueError('service must not be empty')
    if not isinstance(event_day, date):
        raise TypeError('event_day must be a date')
    return (
        resolve_observability_root(volume_path, application=application)
        / _safe_segment(service)
        / f'day={event_day.isoformat()}'
    )


class AtomicJsonlWriter:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def append(self, path: Path, payload: Mapping[str, Any], *, durable: bool = False) -> None:
        if not isinstance(path, Path):
            raise TypeError('path must be a Path')
        if not isinstance(payload, Mapping):
            raise TypeError('payload must be a mapping')
        if not isinstance(durable, bool):
            raise TypeError('durable must be a bool')
        path.parent.mkdir(parents=True, exist_ok=True)
        line = _json_bytes(payload) + b'\n'
        with self._lock:
            descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o640)
            try:
                view = memoryview(line)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError('could not append observability record')
                    view = view[written:]
                if durable:
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)


class AtomicJsonWriter:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def replace(self, path: Path, payload: Mapping[str, Any]) -> None:
        if not isinstance(path, Path):
            raise TypeError('path must be a Path')
        if not isinstance(payload, Mapping):
            raise TypeError('payload must be a mapping')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
        content = _json_bytes(payload) + b'\n'
        with self._lock:
            try:
                with temporary.open('wb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)


class DailyTraceSink(EventSink):
    """Persiste sólo ejecuciones, iteraciones con trabajo e incidencias operacionales."""

    def __init__(
        self,
        volume_path: str | Path,
        *,
        projection: EventProjection | None = None,
        durable_minimum_severity: EventSeverity = EventSeverity.WARNING,
    ) -> None:
        if not isinstance(volume_path, str | Path):
            raise TypeError('volume_path must be a string or Path')
        raw_volume_path = str(volume_path).strip()
        if not raw_volume_path:
            raise ValueError('volume_path must not be empty')
        if projection is not None and not isinstance(projection, EventProjection):
            raise TypeError('projection must be an EventProjection')
        if not isinstance(durable_minimum_severity, EventSeverity):
            raise TypeError('durable_minimum_severity must be an EventSeverity')
        self._volume_path = Path(raw_volume_path)
        self._projection = projection or OperationalEventProjection()
        self._durable_minimum_severity = durable_minimum_severity
        self._jsonl = AtomicJsonlWriter()
        self._snapshot = AtomicJsonWriter()
        self._lock = threading.RLock()
        self._active_scope: tuple[str, str, date] | None = None
        self._summary: dict[str, Any] | None = None

    def emit(
        self,
        event: ObservabilityEvent,
        settings: ObservabilitySettings,
        sanitizer: DataSanitizer,
    ) -> None:
        includes = getattr(self._projection, 'includes', None)
        if callable(includes) and not includes(event):
            return
        payload = event.to_dict(settings=settings, sanitizer=sanitizer)
        projected = self._projection.project(event, payload)
        if projected is None:
            return

        event_day = event.occurred_at_utc.date()
        with self._lock:
            directory = self._directory(settings, event_day)
            service_root = directory.parent
            self._activate_day(settings, event_day, directory, service_root)
            durable = self._is_durable(event.severity)

            if event.name in _EXECUTION_RECORD_EVENTS:
                self._jsonl.append(directory / 'executions.jsonl', projected, durable=True)
            if event.name in _ITERATION_RECORD_EVENTS:
                self._jsonl.append(directory / 'iterations.jsonl', projected, durable=False)
            if event.severity in _ISSUE_SEVERITIES:
                self._jsonl.append(
                    directory / 'issues.jsonl',
                    self._issue_payload(projected, payload),
                    durable=durable,
                )
            if event.name in _LATEST_EVENTS:
                self._snapshot.replace(service_root / 'latest.json', projected)

            assert self._summary is not None
            if self._update_summary(self._summary, event, projected):
                self._snapshot.replace(directory / 'daily-summary.json', self._summary)

    def _directory(self, settings: ObservabilitySettings, event_day: date) -> Path:
        return resolve_observability_day_directory(
            self._volume_path,
            application=settings.application,
            service=settings.service,
            event_day=event_day,
        )

    def _activate_day(
        self,
        settings: ObservabilitySettings,
        event_day: date,
        directory: Path,
        service_root: Path,
    ) -> None:
        scope = (settings.application, settings.service, event_day)
        if self._active_scope == scope and self._summary is not None:
            return
        service_root.mkdir(parents=True, exist_ok=True)
        self._purge_previous_iso_weeks(service_root, event_day)
        directory.mkdir(parents=True, exist_ok=True)
        self._active_scope = scope
        self._summary = self._read_summary(
            directory / 'daily-summary.json',
            settings=settings,
            event_day=event_day,
        )

    @staticmethod
    def _purge_previous_iso_weeks(service_root: Path, reference_day: date) -> None:
        reference_year, reference_week, _ = reference_day.isocalendar()
        resolved_root = service_root.resolve()
        for candidate in service_root.iterdir():
            match = _DAY_DIRECTORY_PATTERN.fullmatch(candidate.name)
            if not match or not candidate.is_dir():
                continue
            try:
                candidate_day = date.fromisoformat(match.group(1))
            except ValueError:
                continue
            candidate_year, candidate_week, _ = candidate_day.isocalendar()
            if (candidate_year, candidate_week) >= (reference_year, reference_week):
                continue
            resolved_candidate = candidate.resolve()
            if resolved_candidate.parent == resolved_root:
                shutil.rmtree(resolved_candidate)

    @staticmethod
    def _read_mapping(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except OSError, json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _read_summary(
        self,
        path: Path,
        *,
        settings: ObservabilitySettings,
        event_day: date,
    ) -> dict[str, Any]:
        summary = self._read_mapping(path)
        if summary.get('day_utc') == event_day.isoformat():
            return summary
        return {
            'day_utc': event_day.isoformat(),
            'application': settings.application,
            'environment': str(settings.environment),
            'service': settings.service,
            'executions': 0,
            'successful': 0,
            'failed': 0,
            'work_iterations': 0,
            'warnings': 0,
            'errors': 0,
            'resource_pressure_events': 0,
            'cpu_peak_percent': 0.0,
            'memory_peak_percent': 0.0,
        }

    @staticmethod
    def _update_summary(
        summary: dict[str, Any],
        event: ObservabilityEvent,
        payload: Mapping[str, Any],
    ) -> bool:
        changed = False
        if event.name in _EXECUTION_RECORD_EVENTS:
            summary['executions'] = int(summary.get('executions', 0)) + 1
            if payload.get('status') == 'success':
                summary['successful'] = int(summary.get('successful', 0)) + 1
            else:
                summary['failed'] = int(summary.get('failed', 0)) + 1
            for key in ('cpu_peak_percent', 'memory_peak_percent'):
                value = payload.get(key)
                if isinstance(value, int | float) and not isinstance(value, bool):
                    summary[key] = max(float(summary.get(key, 0.0)), float(value))
            changed = True
            work_iterations = payload.get('work_iterations')
            if isinstance(work_iterations, int) and not isinstance(work_iterations, bool):
                summary['work_iterations'] = int(summary.get('work_iterations', 0)) + max(
                    0, work_iterations
                )

        if event.severity is EventSeverity.WARNING:
            summary['warnings'] = int(summary.get('warnings', 0)) + 1
            changed = True
        elif event.severity in {EventSeverity.ERROR, EventSeverity.CRITICAL}:
            summary['errors'] = int(summary.get('errors', 0)) + 1
            changed = True
        if (
            event.name.startswith('resource.pressure.')
            and event.name != 'resource.pressure.recovered'
        ):
            summary['resource_pressure_events'] = (
                int(summary.get('resource_pressure_events', 0)) + 1
            )
            changed = True
        if changed:
            summary['updated_at_utc'] = payload['time']
        return changed

    @staticmethod
    def _issue_payload(
        operational: Mapping[str, Any],
        full_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        issue = dict(operational)
        error = full_payload.get('error')
        if isinstance(error, Mapping) and error.get('traceback'):
            issue['traceback'] = error['traceback']
        return issue

    def _is_durable(self, severity: EventSeverity) -> bool:
        order = {
            EventSeverity.DEBUG: 10,
            EventSeverity.INFO: 20,
            EventSeverity.WARNING: 30,
            EventSeverity.ERROR: 40,
            EventSeverity.CRITICAL: 50,
        }
        return order[severity] >= order[self._durable_minimum_severity]
