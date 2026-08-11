from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from atlanticus.kernel import DataSanitizer
from atlanticus.observability import (
    ErrorInfo,
    EventAudience,
    EventCategory,
    EventSeverity,
    ExecutionContext,
    ObservabilityEvent,
    ObservabilitySettings,
    SpanError,
)
from atlanticus.observability_azure import (
    AzureObservabilityBootstrapError,
    build_azure_observability_extension,
)


class _Backend:
    def __init__(self) -> None:
        self.records = []
        self.closed = False

    def emit(self, payload, severity) -> None:
        self.records.append((payload, severity))

    def close(self) -> None:
        self.closed = True


def _settings(tmp_path) -> ObservabilitySettings:
    return ObservabilitySettings.build(
        application='ada',
        service='dispatch-job',
        module='dispatch',
        environment='dev',
        volume_path=tmp_path,
    )


def _day_directory(tmp_path):
    day = datetime.now(UTC).date().isoformat()
    return tmp_path / 'ada' / 'logs' / 'dispatch-job' / f'day={day}'


def test_preview_writes_the_same_compact_operational_payload(tmp_path) -> None:
    settings = _settings(tmp_path)
    extension = build_azure_observability_extension(
        observability_settings=settings,
        environ={
            'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'preview',
            'ATLANTICUS_AZURE_OBSERVABILITY_PROFILE': 'slim',
        },
    )
    sanitizer = DataSanitizer()
    extension.sink.emit(
        ObservabilityEvent(name='dependency.started', category=EventCategory.DEPENDENCY),
        settings,
        sanitizer,
    )
    extension.sink.emit(
        ObservabilityEvent(
            name='data.downloaded',
            category=EventCategory.DATA,
            audience=EventAudience.OPERATIONS,
            metrics={'record_count': 10},
        ),
        settings,
        sanitizer,
    )

    records = [
        json.loads(line)
        for line in (_day_directory(tmp_path) / 'azure-preview.jsonl').read_text().splitlines()
    ]
    assert len(records) == 1
    assert records[0]['event'] == 'data.downloaded'
    assert records[0]['application'] == 'ada'
    assert records[0]['record_count'] == 10


def test_diagnostic_preview_keeps_only_compact_actionable_spans(tmp_path) -> None:
    settings = _settings(tmp_path)
    extension = build_azure_observability_extension(
        observability_settings=settings,
        environ={
            'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'preview',
            'ATLANTICUS_AZURE_OBSERVABILITY_PROFILE': 'diagnostic',
        },
    )
    fast = extension.trace_bridge.start_span(
        'service_bus.receiver.open',
        context=ExecutionContext(
            application='ada',
            environment='dev',
            service='dispatch-job',
            run_id='run-1',
        ),
        attributes={'component': 'atlanticus.connectivity.service_bus'},
    )
    fast.end()
    assert not (_day_directory(tmp_path) / 'azure-diagnostic-spans.jsonl').exists()

    failed = extension.trace_bridge.start_span(
        'blob.sas.download_stream',
        context=ExecutionContext(
            application='ada',
            environment='dev',
            service='dispatch-job',
            run_id='run-1',
            iteration=2,
        ),
        attributes={
            'component': 'atlanticus.connectivity.blob',
            'credential_scope': 'must-not-be-persisted',
        },
    )
    failed.end(SpanError(error_type='TimeoutError', message='download timed out'))

    span = json.loads(
        (_day_directory(tmp_path) / 'azure-diagnostic-spans.jsonl').read_text().splitlines()[0]
    )
    assert span == {
        'time': span['time'],
        'application': 'ada',
        'environment': 'dev',
        'service': 'dispatch-job',
        'run_id': 'run-1',
        'iteration': 2,
        'span': 'blob.sas.download_stream',
        'component': 'atlanticus.connectivity.blob',
        'duration_ms': span['duration_ms'],
        'status': 'error',
        'error_type': 'TimeoutError',
        'error_message': 'download timed out',
    }
    assert 'credential_scope' not in span
    assert not (_day_directory(tmp_path) / 'azure-preview.jsonl').exists()


def test_export_sink_uses_compact_payload_with_injected_backend(tmp_path) -> None:
    backend = _Backend()
    settings = _settings(tmp_path)
    extension = build_azure_observability_extension(
        observability_settings=settings,
        environ={
            'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'export',
            'APPLICATION_INSIGHTS_CONNECTION_STRING': 'InstrumentationKey=fake',
        },
        backend_factory=lambda azure, local: backend,
    )
    event = ObservabilityEvent(
        name='alarm.evaluation.failed',
        category=EventCategory.DATA,
        severity=EventSeverity.ERROR,
        metrics={'alarms_failed': 2},
        error=ErrorInfo.from_exception(RuntimeError('secret-value')),
    )

    extension.sink.emit(event, settings, DataSanitizer())
    extension.sink.close()

    assert backend.records[0][0]['event'] == 'alarm.evaluation.failed'
    assert backend.records[0][0]['application'] == 'ada'
    assert backend.records[0][0]['alarms_failed'] == 2
    assert backend.records[0][0]['error_type'] == 'RuntimeError'
    assert backend.records[0][0]['error_message'] == 'RuntimeError raised'
    assert 'secret-value' not in json.dumps(backend.records[0][0])
    assert backend.records[0][1] is EventSeverity.ERROR
    assert backend.closed


def test_slim_and_diagnostic_export_the_same_operational_json(tmp_path) -> None:
    records = []
    event = ObservabilityEvent(
        name='runtime.iteration.summary',
        category=EventCategory.ITERATION,
        audience=EventAudience.OPERATIONS,
        metrics={'rows': 12},
    )
    for profile in ('slim', 'diagnostic'):
        profile_path = tmp_path / profile
        settings = _settings(profile_path)
        extension = build_azure_observability_extension(
            observability_settings=settings,
            environ={
                'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'preview',
                'ATLANTICUS_AZURE_OBSERVABILITY_PROFILE': profile,
            },
        )
        extension.sink.emit(
            event,
            settings,
            DataSanitizer(),
        )
        records.append(
            json.loads(
                (_day_directory(profile_path) / 'azure-preview.jsonl').read_text().splitlines()[0]
            )
        )

    assert records[0] == records[1]


def test_backend_factory_contract_is_validated_and_error_is_safe(tmp_path) -> None:
    secret = 'InstrumentationKey=must-not-leak'

    with pytest.raises(AzureObservabilityBootstrapError) as captured:
        build_azure_observability_extension(
            observability_settings=_settings(tmp_path),
            environ={
                'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'export',
                'APPLICATION_INSIGHTS_CONNECTION_STRING': secret,
            },
            backend_factory=lambda azure, local: object(),
        )

    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)


def test_partial_backend_is_closed_without_masking_bootstrap_error(tmp_path, monkeypatch) -> None:
    backend = _Backend()

    class _FailingTraceBridge:
        def __init__(self, **kwargs):
            raise RuntimeError('InstrumentationKey=must-not-leak')

    monkeypatch.setattr(
        'atlanticus.observability_azure.bootstrap.AzureMonitorTraceBridge',
        _FailingTraceBridge,
    )

    with pytest.raises(AzureObservabilityBootstrapError, match='bootstrap failed') as captured:
        build_azure_observability_extension(
            observability_settings=_settings(tmp_path),
            environ={
                'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'export',
                'ATLANTICUS_AZURE_OBSERVABILITY_PROFILE': 'diagnostic',
                'APPLICATION_INSIGHTS_CONNECTION_STRING': 'InstrumentationKey=fake',
            },
            backend_factory=lambda azure, local: backend,
        )

    assert backend.closed
    assert 'must-not-leak' not in str(captured.value)


def test_diagnostic_span_snapshots_selected_attributes_and_closes_once(tmp_path) -> None:
    settings = _settings(tmp_path)
    extension = build_azure_observability_extension(
        observability_settings=settings,
        environ={
            'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'preview',
            'ATLANTICUS_AZURE_OBSERVABILITY_PROFILE': 'diagnostic',
        },
    )
    attributes = {
        'component': 'atlanticus.connectivity.blob',
        'source': 'x' * 300,
        'credential_scope': 'must-not-be-persisted',
    }
    handle = extension.trace_bridge.start_span(
        'blob.download',
        context=ExecutionContext(
            application='ada',
            environment='dev',
            service='dispatch-job',
            run_id='run-1',
        ),
        attributes=attributes,
    )
    attributes['component'] = 'mutated'
    handle.end(SpanError(error_type='TimeoutError', message='TimeoutError raised'))
    handle.end(SpanError(error_type='RuntimeError', message='RuntimeError raised'))

    records = [
        json.loads(line)
        for line in (_day_directory(tmp_path) / 'azure-diagnostic-spans.jsonl')
        .read_text()
        .splitlines()
    ]
    assert len(records) == 1
    assert records[0]['component'] == 'atlanticus.connectivity.blob'
    assert len(records[0]['source']) == 256
    assert records[0]['source'].endswith('…')
    assert 'credential_scope' not in records[0]
