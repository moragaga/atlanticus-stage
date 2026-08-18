from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Lock
from time import sleep
from types import SimpleNamespace

import pytest

import atlanticus.data_producers.pi.timeout_retry as timeout_retry_module
from atlanticus.data_producers.pi import (
    PiAcquisitionWindow,
    PiDataProducerAcquisitionError,
    PiDataProducerTimeoutExhaustedError,
    PiExecutionPlan,
    PiStreamSetAcquirer,
    ResolvedPiTag,
)
from atlanticus.integrations.pi.contracts import (
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
)
from atlanticus.integrations.pi.web_api import (
    PiWebApiConnectionError,
    PiWebApiLimits,
    PiWebApiStatusError,
    PiWebApiTimeoutError,
)
from atlanticus.runtime import JobDefinition, JobRuntimeContext, RuntimeConfiguration


class FakeStreamSets:
    def __init__(self) -> None:
        self.interpolated_calls = []
        self.recorded_calls = []
        self.interpolated_handler = lambda web_ids, start, end, interval: ()
        self.recorded_handler = lambda web_ids, start, end: ()

    def get_interpolated(
        self,
        web_ids,
        *,
        start_time_utc,
        end_time_utc,
        interpolation_seconds,
    ):
        self.interpolated_calls.append(
            (tuple(web_ids), start_time_utc, end_time_utc, interpolation_seconds)
        )
        return self.interpolated_handler(
            tuple(web_ids),
            start_time_utc,
            end_time_utc,
            interpolation_seconds,
        )

    def get_recorded(self, web_ids, *, start_time_utc, end_time_utc):
        self.recorded_calls.append((tuple(web_ids), start_time_utc, end_time_utc))
        return self.recorded_handler(tuple(web_ids), start_time_utc, end_time_utc)


class FakeClient:
    def __init__(self, *, interpolated_limit=100, recorded_limit=100) -> None:
        self.settings = SimpleNamespace(
            limits=PiWebApiLimits(
                interpolated_max_web_ids=interpolated_limit,
                recorded_max_web_ids=recorded_limit,
            )
        )
        self.streamsets = FakeStreamSets()


def _tag(name: str, mode: PiExtractionMode) -> ResolvedPiTag:
    return ResolvedPiTag(
        definition=PiTagDefinition(
            tag_name=name,
            alias=name.lower(),
            value_kind=PiValueKind.NUMBER,
            extraction_mode=mode,
            materializations=(PiMaterialization.DAILY,),
        ),
        web_id=f'WEB_{name}',
    )


def _context(tmp_path) -> JobRuntimeContext:
    definition = JobDefinition(
        module_name='tests.pi_acquisition',
        service_name='pi-web-api',
        execution_timeout_seconds=30,
        shutdown_grace_seconds=1,
        iteration_timeout_seconds=10,
    )
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='run-id',
        correlation_id='correlation-id',
    )
    context._begin_iteration(1)
    return context


def _window(slot_count: int, *, interpolation_seconds: int = 10) -> PiAcquisitionWindow:
    start = datetime(2026, 8, 15, 10, tzinfo=UTC)
    return PiAcquisitionWindow(
        first_slot_utc=start,
        last_slot_utc=start + timedelta(seconds=(slot_count - 1) * interpolation_seconds),
        interpolation_seconds=interpolation_seconds,
    )


def _raise_timeout(phase: str):
    raise PiWebApiTimeoutError(phase=phase)


def test_acquirer_chunks_web_ids_by_integration_limits(tmp_path) -> None:
    client = FakeClient(interpolated_limit=2)
    tags = tuple(_tag(f'TAG_{index}', PiExtractionMode.INTERPOLATED) for index in range(5))
    plan = PiExecutionPlan(interpolated=tags, recorded=())

    result = PiStreamSetAcquirer(
        client=client,
        interpolated_max_parallel_requests=1,
    ).acquire(
        plan=plan,
        window=_window(1),
        context=_context(tmp_path),
    )

    assert [call[0] for call in client.streamsets.interpolated_calls] == [
        ('WEB_TAG_0', 'WEB_TAG_1'),
        ('WEB_TAG_2', 'WEB_TAG_3'),
        ('WEB_TAG_4',),
    ]
    assert result.interpolated_request_count == 3


def test_acquirer_segments_interpolated_windows_by_process_point_guard(tmp_path) -> None:
    client = FakeClient(interpolated_limit=100)
    plan = PiExecutionPlan(
        interpolated=(
            _tag('TAG_A', PiExtractionMode.INTERPOLATED),
            _tag('TAG_B', PiExtractionMode.INTERPOLATED),
        ),
        recorded=(),
    )

    result = PiStreamSetAcquirer(client=client, max_data_points=6).acquire(
        plan=plan,
        window=_window(5),
        context=_context(tmp_path),
    )

    assert result.interpolated_request_count == 3
    assert [call[1] for call in client.streamsets.interpolated_calls] == [
        datetime(2026, 8, 15, 10, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 15, 10, 0, 20, tzinfo=UTC),
        datetime(2026, 8, 15, 10, 0, 40, tzinfo=UTC),
    ]


def test_acquirer_retries_timeout_and_recovers_without_splitting(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(timeout_retry_module, '_TIMEOUT_RETRY_DELAYS_SECONDS', (0.0, 0.0, 0.0))
    client = FakeClient()
    plan = PiExecutionPlan(
        interpolated=(_tag('TAG_A', PiExtractionMode.INTERPOLATED),),
        recorded=(),
    )
    attempts = 0

    def handler(web_ids, start, end, interval):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PiWebApiTimeoutError(phase='connect')
        return ({'name': 'TAG_A', 'timestamp': start.isoformat(), 'value': 1},)

    client.streamsets.interpolated_handler = handler
    context = _context(tmp_path)
    result = PiStreamSetAcquirer(client=client).acquire(
        plan=plan,
        window=_window(13),
        context=context,
    )

    assert attempts == 3
    assert context.get_execution_fact('pi_timeout_retries') == 2
    assert result.interpolated_request_count == 3
    assert result.split_count == 0
    assert len(result.interpolated) == 1


def test_acquirer_exhausts_three_timeout_retries_without_splitting(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(timeout_retry_module, '_TIMEOUT_RETRY_DELAYS_SECONDS', (0.0, 0.0, 0.0))
    client = FakeClient()
    plan = PiExecutionPlan(
        interpolated=(_tag('TAG_A', PiExtractionMode.INTERPOLATED),),
        recorded=(),
    )
    client.streamsets.interpolated_handler = lambda web_ids, start, end, interval: _raise_timeout(
        'connect'
    )

    context = _context(tmp_path)
    with pytest.raises(PiDataProducerTimeoutExhaustedError) as captured:
        PiStreamSetAcquirer(client=client).acquire(
            plan=plan,
            window=_window(13),
            context=context,
        )

    error = captured.value
    assert context.get_execution_fact('pi_timeout_retries') == 3
    assert error.phase == 'connect'
    assert error.retry_count == 3
    assert error.request_count == 4
    assert error.interpolated_request_count == 4
    assert error.recorded_request_count == 0
    assert error.split_count == 0
    assert len(client.streamsets.interpolated_calls) == 4


def test_acquirer_splits_only_recoverable_failed_window(tmp_path) -> None:
    client = FakeClient()
    plan = PiExecutionPlan(
        interpolated=(_tag('TAG_A', PiExtractionMode.INTERPOLATED),),
        recorded=(),
    )

    def handler(web_ids, start, end, interval):
        if (end - start).total_seconds() > 60:
            raise PiWebApiConnectionError('PI Web API request failed')
        return (
            {
                'name': 'TAG_A',
                'timestamp': start.isoformat(),
                'value': 1,
            },
        )

    client.streamsets.interpolated_handler = handler
    result = PiStreamSetAcquirer(client=client).acquire(
        plan=plan,
        window=_window(13),
        context=_context(tmp_path),
    )

    assert result.split_count == 2
    assert result.interpolated_request_count == 5
    assert len(result.interpolated) == 3


def test_acquirer_does_not_split_nonrecoverable_status(tmp_path) -> None:
    client = FakeClient()
    plan = PiExecutionPlan(
        interpolated=(_tag('TAG_A', PiExtractionMode.INTERPOLATED),),
        recorded=(),
    )

    def handler(web_ids, start, end, interval):
        raise PiWebApiStatusError(status_code=401)

    client.streamsets.interpolated_handler = handler
    with pytest.raises(PiWebApiStatusError, match='status 401'):
        PiStreamSetAcquirer(client=client).acquire(
            plan=plan,
            window=_window(13),
            context=_context(tmp_path),
        )

    assert len(client.streamsets.interpolated_calls) == 1


def test_recorded_exact_duplicate_uses_last_received_value(tmp_path) -> None:
    client = FakeClient()
    plan = PiExecutionPlan(
        interpolated=(),
        recorded=(_tag('TAG_A', PiExtractionMode.RECORDED),),
    )
    timestamp = '2026-08-15T10:00:03Z'
    client.streamsets.recorded_handler = lambda web_ids, start, end: (
        {'name': 'TAG_A', 'timestamp': timestamp, 'value': 1},
        {'name': 'TAG_A', 'timestamp': timestamp, 'value': 2},
        {'name': 'OTHER', 'timestamp': timestamp, 'value': 3},
        {'name': 'TAG_A', 'timestamp': end.isoformat(), 'value': 4},
    )

    result = PiStreamSetAcquirer(client=client).acquire(
        plan=plan,
        window=_window(1),
        context=_context(tmp_path),
    )

    assert [(sample.tag_name, sample.value) for sample in result.recorded] == [('TAG_A', 2)]
    assert result.recorded_conflict_count == 1
    assert result.unexpected_record_count == 1


def test_recorded_windows_are_not_segmented_by_interpolated_point_guard(tmp_path) -> None:
    client = FakeClient(recorded_limit=100)
    plan = PiExecutionPlan(
        interpolated=(),
        recorded=(
            _tag('TAG_A', PiExtractionMode.RECORDED),
            _tag('TAG_B', PiExtractionMode.RECORDED),
        ),
    )

    result = PiStreamSetAcquirer(client=client, max_data_points=4).acquire(
        plan=plan,
        window=_window(5),
        context=_context(tmp_path),
    )

    assert result.recorded_request_count == 1
    assert len(client.streamsets.recorded_calls) == 1
    assert client.streamsets.recorded_calls[0][1:] == (
        datetime(2026, 8, 15, 10, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 15, 10, 0, 50, tzinfo=UTC),
    )


def test_recorded_response_over_point_guard_splits_until_response_fits(tmp_path) -> None:
    client = FakeClient()
    plan = PiExecutionPlan(
        interpolated=(),
        recorded=(_tag('TAG_A', PiExtractionMode.RECORDED),),
    )

    def handler(web_ids, start, end):
        if (end - start).total_seconds() > 60:
            return (
                {'name': 'TAG_A', 'timestamp': start.isoformat(), 'value': 1},
                {
                    'name': 'TAG_A',
                    'timestamp': (start + timedelta(seconds=1)).isoformat(),
                    'value': 2,
                },
            )
        return ({'name': 'TAG_A', 'timestamp': start.isoformat(), 'value': 1},)

    client.streamsets.recorded_handler = handler
    result = PiStreamSetAcquirer(client=client, max_data_points=1).acquire(
        plan=plan,
        window=_window(13),
        context=_context(tmp_path),
    )

    assert result.split_count == 2
    assert result.recorded_request_count == 5
    assert len(result.recorded) == 3


def test_response_over_point_guard_fails_at_minimum_window(tmp_path) -> None:
    client = FakeClient()
    plan = PiExecutionPlan(
        interpolated=(),
        recorded=(_tag('TAG_A', PiExtractionMode.RECORDED),),
    )
    client.streamsets.recorded_handler = lambda web_ids, start, end: (
        {'name': 'TAG_A', 'timestamp': start.isoformat(), 'value': 1},
        {'name': 'TAG_A', 'timestamp': (start + timedelta(seconds=1)).isoformat(), 'value': 2},
    )

    with pytest.raises(PiDataProducerAcquisitionError, match='response exceeds'):
        PiStreamSetAcquirer(client=client, max_data_points=1).acquire(
            plan=plan,
            window=_window(1),
            context=_context(tmp_path),
        )


def test_interpolated_chunks_use_bounded_parallel_requests_and_keep_result_order(tmp_path) -> None:
    client = FakeClient(interpolated_limit=1)
    tags = tuple(_tag(f'TAG_{index}', PiExtractionMode.INTERPOLATED) for index in range(5))
    plan = PiExecutionPlan(interpolated=tags, recorded=())
    lock = Lock()
    active = 0
    peak = 0

    def handler(web_ids, start, end, interval):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        sleep(0.03)
        with lock:
            active -= 1
        name = web_ids[0].removeprefix('WEB_')
        return ({'name': name, 'timestamp': start.isoformat(), 'value': name},)

    client.streamsets.interpolated_handler = handler
    result = PiStreamSetAcquirer(
        client=client,
        interpolated_max_parallel_requests=3,
    ).acquire(
        plan=plan,
        window=_window(1),
        context=_context(tmp_path),
    )

    assert peak == 3
    assert result.interpolated_request_count == 5
    assert [sample.tag_name for sample in result.interpolated] == [
        'TAG_0',
        'TAG_1',
        'TAG_2',
        'TAG_3',
        'TAG_4',
    ]


@pytest.mark.parametrize('value', [0, 4, True])
def test_acquirer_rejects_invalid_interpolated_parallel_limit(value) -> None:
    with pytest.raises(ValueError, match='between 1 and 3'):
        PiStreamSetAcquirer(
            client=FakeClient(),
            interpolated_max_parallel_requests=value,
        )


def test_parallel_interpolated_timeout_exhaustion_preserves_aggregate_request_counts(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(timeout_retry_module, '_TIMEOUT_RETRY_DELAYS_SECONDS', (0.0, 0.0, 0.0))
    client = FakeClient(interpolated_limit=1)
    tags = tuple(_tag(f'TAG_{index}', PiExtractionMode.INTERPOLATED) for index in range(4))
    plan = PiExecutionPlan(interpolated=tags, recorded=())

    def handler(web_ids, start, end, interval):
        if web_ids == ('WEB_TAG_1',):
            _raise_timeout('read')
        name = web_ids[0].removeprefix('WEB_')
        return ({'name': name, 'timestamp': start.isoformat(), 'value': name},)

    client.streamsets.interpolated_handler = handler
    context = _context(tmp_path)

    with pytest.raises(PiDataProducerTimeoutExhaustedError) as raised:
        PiStreamSetAcquirer(
            client=client,
            interpolated_max_parallel_requests=3,
        ).acquire(
            plan=plan,
            window=_window(1),
            context=context,
        )

    assert raised.value.phase == 'read'
    assert raised.value.retry_count == 3
    assert raised.value.interpolated_request_count == 7
    assert raised.value.recorded_request_count == 0
    assert raised.value.request_count == 7
    assert context.get_execution_fact('pi_timeout_retries') == 3
