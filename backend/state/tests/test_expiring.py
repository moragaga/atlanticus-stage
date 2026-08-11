from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlanticus.state import AtomicStateStore, ExpiringKeySet, StateKey, StateValidationError


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


def _key_set(tmp_path: Path, clock: MutableClock, max_entries: int = 10) -> ExpiringKeySet:
    store = AtomicStateStore(volume_path=tmp_path, application='ada', clock=clock)
    return ExpiringKeySet(
        store=store,
        key=StateKey(namespace=('ingestion', 'service-bus', 'state'), name='messages'),
        retention_seconds=60,
        max_entries=max_entries,
        clock=clock,
    )


def test_batch_operations_store_hashes_instead_of_raw_identifiers(tmp_path: Path) -> None:
    clock = MutableClock()
    key_set = _key_set(tmp_path, clock)

    assert key_set.contains_many(('message-1', 'message-2')) == (False, False)
    assert key_set.add_many(('message-1', 'message-2')) == 2
    assert key_set.contains_many(('message-1', 'message-3')) == (True, False)

    state_content = next(tmp_path.rglob('messages.json')).read_text(encoding='utf-8')
    assert 'message-1' not in state_content
    assert 'message-2' not in state_content


def test_expired_entries_are_removed_and_not_accumulated(tmp_path: Path) -> None:
    clock = MutableClock()
    key_set = _key_set(tmp_path, clock)
    key_set.add('message-1')

    clock.advance(61)

    assert key_set.contains('message-1') is False
    assert key_set.count() == 0


def test_capacity_evicts_oldest_expiry(tmp_path: Path) -> None:
    clock = MutableClock()
    key_set = _key_set(tmp_path, clock, max_entries=2)
    key_set.add('oldest')
    clock.advance(1)
    key_set.add_many(('newer-1', 'newer-2'))

    assert key_set.contains_many(('oldest', 'newer-1', 'newer-2')) == (False, True, True)


def test_expiring_set_rejects_invalid_dependencies(tmp_path: Path) -> None:
    clock = MutableClock()
    store = AtomicStateStore(volume_path=tmp_path, application='ada', clock=clock)
    key = StateKey(namespace=('deduplication',), name='messages')

    with pytest.raises(StateValidationError, match='AtomicStateStore'):
        ExpiringKeySet(
            store=object(),
            key=key,
            retention_seconds=60,
            max_entries=10,
        )
    with pytest.raises(StateValidationError, match='StateKey'):
        ExpiringKeySet(
            store=store,
            key='messages',
            retention_seconds=60,
            max_entries=10,
        )
    with pytest.raises(StateValidationError, match='clock'):
        ExpiringKeySet(
            store=store,
            key=key,
            retention_seconds=60,
            max_entries=10,
            clock='invalid',
        )


def test_expiring_set_ignores_observability_failures(tmp_path: Path) -> None:
    class FailingLogger:
        def log(self, *_: object, **__: object) -> bool:
            raise RuntimeError('telemetry unavailable')

    clock = MutableClock()
    store = AtomicStateStore(volume_path=tmp_path, application='ada', clock=clock)
    key_set = ExpiringKeySet(
        store=store,
        key=StateKey(namespace=('deduplication',), name='messages'),
        retention_seconds=60,
        max_entries=1,
        clock=clock,
        logger=FailingLogger(),
    )

    assert key_set.add_many(('first', 'second')) == 1
    assert sum(key_set.contains_many(('first', 'second'))) == 1
