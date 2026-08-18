from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from atlanticus.data_producers.notpii import NotPiiBatch, NotPiiSourceError
from atlanticus.data_producers.notpii.job import NotPiiJob
from atlanticus.data_producers.notpii.models import NotPiiProcessingResult
from atlanticus.data_producers.notpii.producer_state import NotPiiProducerState
from atlanticus.datasets.models import DatasetKey, DatasetTarget
from atlanticus.datasets.results import (
    DatasetPublicationResult,
    PublicationQuality,
    PublicationStatus,
)
from atlanticus.integrations.pi.contracts import PiExtractionMode
from atlanticus.state import AtomicStateStore


class FakeLogger:
    def warning(self, *_: object, **__: object) -> None:
        pass


class FakeContext:
    def __init__(self) -> None:
        self.logger = FakeLogger()
        self.iteration_work = False
        self.iteration_facts: dict[str, object] = {}
        self.execution_facts: dict[str, object] = {}

    def mark_iteration_work(self) -> None:
        self.iteration_work = True

    def set_iteration_fact(self, key: str, value: object) -> None:
        self.iteration_facts[key] = value

    def increment_iteration_counter(self, key: str, amount: int | float = 1) -> None:
        self.iteration_facts[key] = self.iteration_facts.get(key, 0) + amount

    def set_execution_fact(self, key: str, value: object) -> None:
        self.execution_facts[key] = value

    def get_execution_fact(self, key: str) -> object | None:
        return self.execution_facts.get(key)

    def increment_execution_counter(self, key: str, amount: int | float = 1) -> None:
        self.execution_facts[key] = self.execution_facts.get(key, 0) + amount

    def raise_if_cancelled(self) -> None:
        pass


class FakeDelivery:
    def __init__(self, message_id: str, events: list[str], *, fail_complete: bool = False) -> None:
        self.message = SimpleNamespace(message_id=message_id)
        self.events = events
        self.fail_complete = fail_complete
        self.can_settle = True
        self.completed = False
        self.abandoned = False
        self.dead_lettered = False

    @contextmanager
    def auto_renew_lock(self, *, max_duration_seconds: float):
        assert max_duration_seconds == 240
        self.events.append(f'renew:{self.message.message_id}')
        yield

    def complete(self) -> None:
        self.events.append(f'complete:{self.message.message_id}')
        if self.fail_complete:
            raise RuntimeError('controlled complete failure')
        self.can_settle = False
        self.completed = True

    def abandon(self) -> None:
        if self.can_settle:
            self.events.append(f'abandon:{self.message.message_id}')
            self.can_settle = False
            self.abandoned = True

    def dead_letter(self, *, reason: str | None, error_description: str | None) -> None:
        assert reason == 'InvalidNotPiiPayload'
        assert error_description
        self.events.append(f'dead-letter:{self.message.message_id}')
        self.can_settle = False
        self.dead_lettered = True


class FakeReceiver:
    def __init__(self, batches: list[tuple[FakeDelivery, ...]]) -> None:
        self.batches = list(batches)
        self.requested: list[int] = []

    def receive_batch(self, *, max_message_count: int):
        self.requested.append(max_message_count)
        return self.batches.pop(0) if self.batches else ()


class FakeProcessor:
    def __init__(
        self,
        mode: PiExtractionMode,
        events: list[str],
        *,
        invalid_id: str | None = None,
        relevant_data: bool = True,
    ) -> None:
        self.mode = mode
        self.events = events
        self.invalid_id = invalid_id
        self.relevant_data = relevant_data

    def read(self, message) -> NotPiiBatch:
        message_id = str(message.message_id)
        self.events.append(f'read:{message_id}')
        if message_id == self.invalid_id:
            raise NotPiiSourceError('controlled invalid payload')
        data = (
            pd.DataFrame(
                {
                    'timestamp_utc': [datetime(2026, 8, 15, 12, 0, tzinfo=UTC)],
                    'value': [1.0],
                }
            )
            if self.relevant_data
            else pd.DataFrame(columns=('timestamp_utc', 'value'))
        )
        return NotPiiBatch(
            message_id=message_id,
            data=data,
            extraction_mode=self.mode,
        )

    def publish(self, batches) -> NotPiiProcessingResult:
        resolved = tuple(batches)
        self.events.append('publish')
        row_count = sum(len(item.data) for item in resolved)
        publications = (_committed_publication(),) if row_count else ()
        return NotPiiProcessingResult(
            message_count=len(resolved),
            row_count=row_count,
            materialized_row_count=1 if row_count else 0,
            publications=publications,
            source_last_updated_at_utc=(
                datetime(2026, 8, 15, 12, 0, tzinfo=UTC) if row_count else None
            ),
        )


def _committed_publication() -> DatasetPublicationResult:
    return DatasetPublicationResult(
        target=DatasetTarget(
            dataset=DatasetKey(namespace=('tests',), name='notpii'),
            materialization='latest',
        ),
        status=PublicationStatus.COMMITTED,
        quality=PublicationQuality.SUCCESS,
        finished_at_utc=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
        duration_ms=1.0,
        item_count=1,
        artifact_count=1,
    )


class ObservedProducerState(NotPiiProducerState):
    def __init__(self, *, store: AtomicStateStore, events: list[str], fail: bool = False) -> None:
        super().__init__(store=store)
        self.events = events
        self.fail = fail

    def advance(self, observations):
        self.events.append('state')
        if self.fail:
            raise RuntimeError('controlled state failure')
        return super().advance(observations)


def _state(tmp_path, events: list[str], *, fail: bool = False) -> ObservedProducerState:
    return ObservedProducerState(
        store=AtomicStateStore(volume_path=tmp_path, application='ada'),
        events=events,
        fail=fail,
    )


def test_batch_materializes_once_then_commits_state_then_completes(tmp_path) -> None:
    events: list[str] = []
    deliveries = tuple(FakeDelivery(f'm{index}', events) for index in range(1, 4))
    receiver = FakeReceiver([deliveries])
    processor = FakeProcessor(PiExtractionMode.INTERPOLATED, events)
    job = NotPiiJob(
        receivers={PiExtractionMode.INTERPOLATED: receiver},
        processors={PiExtractionMode.INTERPOLATED: processor},
        producer_state=_state(tmp_path, events),
        max_message_count=10,
    )
    context = FakeContext()

    job.run_iteration(context)

    assert receiver.requested == [10]
    assert events.count('publish') == 1
    assert events.index('publish') < events.index('state') < events.index('complete:m1')
    assert all(item.completed for item in deliveries)
    assert context.iteration_facts['outcome'] == 'completed'
    assert context.execution_facts['messages_completed'] == 3


def test_active_modes_run_in_order_within_same_iteration(tmp_path) -> None:
    events: list[str] = []
    interpolated_delivery = FakeDelivery('i1', events)
    recorded_delivery = FakeDelivery('r1', events)
    interpolated = FakeReceiver([(interpolated_delivery,)])
    recorded = FakeReceiver([(recorded_delivery,)])
    job = NotPiiJob(
        receivers={
            PiExtractionMode.INTERPOLATED: interpolated,
            PiExtractionMode.RECORDED: recorded,
        },
        processors={
            PiExtractionMode.INTERPOLATED: FakeProcessor(PiExtractionMode.INTERPOLATED, events),
            PiExtractionMode.RECORDED: FakeProcessor(PiExtractionMode.RECORDED, events),
        },
        producer_state=_state(tmp_path, events),
        max_message_count=10,
    )
    context = FakeContext()

    job.run_iteration(context)

    assert interpolated.requested == [10]
    assert recorded.requested == [10]
    assert interpolated_delivery.completed is True
    assert recorded_delivery.completed is True
    assert events.index('complete:i1') < events.index('read:r1')
    assert context.iteration_facts['extraction_modes'] == 'interpolated,recorded'
    assert context.iteration_facts['messages_received'] == 2
    assert context.iteration_facts['messages_completed'] == 2
    assert context.iteration_facts['outcome'] == 'completed'


def test_empty_interpolated_does_not_skip_recorded(tmp_path) -> None:
    events: list[str] = []
    recorded_delivery = FakeDelivery('r1', events)
    interpolated = FakeReceiver([()])
    recorded = FakeReceiver([(recorded_delivery,)])
    job = NotPiiJob(
        receivers={
            PiExtractionMode.INTERPOLATED: interpolated,
            PiExtractionMode.RECORDED: recorded,
        },
        processors={
            PiExtractionMode.INTERPOLATED: FakeProcessor(PiExtractionMode.INTERPOLATED, events),
            PiExtractionMode.RECORDED: FakeProcessor(PiExtractionMode.RECORDED, events),
        },
        producer_state=_state(tmp_path, events),
        max_message_count=10,
    )
    context = FakeContext()

    job.run_iteration(context)

    assert interpolated.requested == [10]
    assert recorded.requested == [10]
    assert recorded_delivery.completed is True
    assert context.iteration_facts['messages_received'] == 1
    assert context.iteration_facts['messages_completed'] == 1
    assert context.iteration_facts['outcome'] == 'completed'


def test_invalid_message_is_dead_lettered_and_rest_of_batch_is_abandoned(tmp_path) -> None:
    events: list[str] = []
    deliveries = (
        FakeDelivery('valid-before', events),
        FakeDelivery('invalid', events),
        FakeDelivery('valid-after', events),
    )
    job = NotPiiJob(
        receivers={PiExtractionMode.INTERPOLATED: FakeReceiver([deliveries])},
        processors={
            PiExtractionMode.INTERPOLATED: FakeProcessor(
                PiExtractionMode.INTERPOLATED,
                events,
                invalid_id='invalid',
            )
        },
        producer_state=_state(tmp_path, events),
        max_message_count=10,
    )
    context = FakeContext()

    job.run_iteration(context)

    assert deliveries[1].dead_lettered is True
    assert deliveries[0].abandoned is True
    assert deliveries[2].abandoned is True
    assert 'publish' not in events
    assert 'state' not in events
    assert context.iteration_facts['outcome'] == 'skipped'
    assert context.iteration_work is True
    assert context.iteration_facts['reason'] == 'invalid_message'


def test_state_failure_abandons_whole_batch_without_completing(tmp_path) -> None:
    events: list[str] = []
    deliveries = (FakeDelivery('m1', events), FakeDelivery('m2', events))
    job = NotPiiJob(
        receivers={PiExtractionMode.INTERPOLATED: FakeReceiver([deliveries])},
        processors={
            PiExtractionMode.INTERPOLATED: FakeProcessor(PiExtractionMode.INTERPOLATED, events)
        },
        producer_state=_state(tmp_path, events, fail=True),
        max_message_count=10,
    )

    with pytest.raises(RuntimeError, match='controlled state failure'):
        job.run_iteration(FakeContext())

    assert all(item.abandoned for item in deliveries)
    assert not any(item.completed for item in deliveries)
    assert events.index('publish') < events.index('state') < events.index('abandon:m1')


def test_empty_receive_is_skipped_without_state_change(tmp_path) -> None:
    events: list[str] = []
    state = _state(tmp_path, events)
    job = NotPiiJob(
        receivers={PiExtractionMode.INTERPOLATED: FakeReceiver([()])},
        processors={
            PiExtractionMode.INTERPOLATED: FakeProcessor(PiExtractionMode.INTERPOLATED, events)
        },
        producer_state=state,
        max_message_count=10,
    )
    context = FakeContext()

    job.run_iteration(context)

    assert context.iteration_work is False
    assert context.iteration_facts['outcome'] == 'skipped'
    assert context.iteration_facts['reason'] == 'no_message'
    assert state.current().revision == 0


def test_messages_without_relevant_data_are_completed_and_iteration_is_skipped(tmp_path) -> None:
    events: list[str] = []
    deliveries = tuple(FakeDelivery(f'm{index}', events) for index in range(1, 4))
    state = _state(tmp_path, events)
    job = NotPiiJob(
        receivers={PiExtractionMode.RECORDED: FakeReceiver([deliveries])},
        processors={
            PiExtractionMode.RECORDED: FakeProcessor(
                PiExtractionMode.RECORDED,
                events,
                relevant_data=False,
            )
        },
        producer_state=state,
        max_message_count=10,
    )
    context = FakeContext()

    job.run_iteration(context)

    assert all(item.completed for item in deliveries)
    assert context.iteration_work is False
    assert context.iteration_facts['messages_received'] == 3
    assert context.iteration_facts['messages_completed'] == 3
    assert context.iteration_facts['rows_received'] == 0
    assert context.iteration_facts['rows_materialized'] == 0
    assert context.iteration_facts['publications'] == 0
    assert context.iteration_facts['publications_committed'] == 0
    assert context.iteration_facts['outcome'] == 'skipped'
    assert context.iteration_facts['reason'] == 'no_relevant_data'
    assert state.current().revision == 0


def test_irrelevant_interpolated_does_not_hide_recorded_materialization(tmp_path) -> None:
    events: list[str] = []
    interpolated_delivery = FakeDelivery('i1', events)
    recorded_delivery = FakeDelivery('r1', events)
    job = NotPiiJob(
        receivers={
            PiExtractionMode.INTERPOLATED: FakeReceiver([(interpolated_delivery,)]),
            PiExtractionMode.RECORDED: FakeReceiver([(recorded_delivery,)]),
        },
        processors={
            PiExtractionMode.INTERPOLATED: FakeProcessor(
                PiExtractionMode.INTERPOLATED,
                events,
                relevant_data=False,
            ),
            PiExtractionMode.RECORDED: FakeProcessor(PiExtractionMode.RECORDED, events),
        },
        producer_state=_state(tmp_path, events),
        max_message_count=10,
    )
    context = FakeContext()

    job.run_iteration(context)

    assert interpolated_delivery.completed is True
    assert recorded_delivery.completed is True
    assert events.index('complete:i1') < events.index('read:r1')
    assert context.iteration_work is True
    assert context.iteration_facts['messages_received'] == 2
    assert context.iteration_facts['messages_completed'] == 2
    assert context.iteration_facts['rows_received'] == 1
    assert context.iteration_facts['publications_committed'] == 1
    assert context.iteration_facts['outcome'] == 'completed'
