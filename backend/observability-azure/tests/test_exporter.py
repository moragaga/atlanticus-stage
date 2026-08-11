from __future__ import annotations

import json
import logging

import pytest

from atlanticus.observability import EventSeverity
from atlanticus.observability_azure import OpenTelemetryLogBackend


class _Logger:
    def __init__(self) -> None:
        self.records = []
        self.removed = []

    def log(self, level, message, *, extra) -> None:
        self.records.append((level, message, extra))

    def removeHandler(self, handler) -> None:
        self.removed.append(handler)


class _Provider:
    def __init__(self) -> None:
        self.flushed = []
        self.shutdown_count = 0

    def force_flush(self, *, timeout_millis) -> None:
        self.flushed.append(timeout_millis)

    def shutdown(self) -> None:
        self.shutdown_count += 1


def _backend() -> OpenTelemetryLogBackend:
    backend = OpenTelemetryLogBackend.__new__(OpenTelemetryLogBackend)
    backend._flush_timeout_millis = 3000
    backend._provider = _Provider()
    backend._handler = object()
    backend._logger = _Logger()
    backend._closed = False
    return backend


def test_emit_places_the_complete_compact_json_in_the_log_body() -> None:
    backend = _backend()
    payload = {
        'event': 'execution.completed',
        'application': 'ada',
        'environment': 'dev',
        'service': 'dispatch-job',
        'run_id': 'run-1',
        'rows': 120,
        'error_type': 'TimeoutError',
        'error_message': 'TimeoutError raised',
    }

    backend.emit(payload, EventSeverity.ERROR)

    level, message, dimensions = backend._logger.records[0]
    assert level == logging.ERROR
    assert json.loads(message) == payload
    assert dimensions == {
        'atlanticus_event_name': 'execution.completed',
        'atlanticus_application': 'ada',
        'atlanticus_environment': 'dev',
        'atlanticus_service': 'dispatch-job',
        'atlanticus_run_id': 'run-1',
    }


def test_close_is_idempotent_and_emit_after_close_is_rejected() -> None:
    backend = _backend()

    backend.close()
    backend.close()

    assert backend._provider.flushed == [3000]
    assert backend._provider.shutdown_count == 1
    assert backend._logger.removed == [backend._handler]
    with pytest.raises(RuntimeError, match='closed'):
        backend.emit({}, EventSeverity.INFO)
