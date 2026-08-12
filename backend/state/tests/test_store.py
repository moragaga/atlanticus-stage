from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import atlanticus.state.store as store_module
from atlanticus.state import (
    AtomicStateStore,
    StateCorruptionError,
    StateKey,
    StateTooLargeError,
    StateValidationError,
    StateWriteError,
)


class CapturingLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log(self, severity: Any, message: str, **values: Any) -> bool:
        self.events.append({'severity': severity, 'message': message, **values})
        return True


def _key() -> StateKey:
    return StateKey(namespace=('ingestion', 'pi', 'state'), name='publication')


def _store(tmp_path: Path, **values: Any) -> AtomicStateStore:
    return AtomicStateStore(
        volume_path=tmp_path,
        application='ada',
        clock=lambda: datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
        **values,
    )


def test_store_round_trip_uses_application_scope(tmp_path: Path) -> None:
    store = _store(tmp_path)

    written = store.replace(_key(), {'status': 'warning', 'change_token': 'abc'})
    read = store.read(_key())

    assert read == written
    assert store.path_for(_key()) == (
        tmp_path / 'ada' / '.runtime' / 'state' / 'ingestion' / 'pi' / 'state' / 'publication.json'
    )
    assert store.application_root == tmp_path / 'ada'
    assert store.state_root == tmp_path / 'ada' / '.runtime' / 'state'
    assert store.path_for(_key()).read_text(encoding='utf-8').endswith('\n')


def test_missing_state_returns_none(tmp_path: Path) -> None:
    assert _store(tmp_path).read(_key()) is None


def test_corrupt_state_is_not_treated_as_missing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = store.path_for(_key())
    path.parent.mkdir(parents=True)
    path.write_text('{not-json', encoding='utf-8')

    with pytest.raises(StateCorruptionError):
        store.read(_key())


@pytest.mark.parametrize(
    'content',
    [
        b'{"schema_version":1,"schema_version":1,"updated_at_utc":"x","value":{}}',
        b'\xff',
        b'{"schema_version":1,"updated_at_utc":"2026-07-20T15:00:00Z","value":{"x":1e999}}',
    ],
)
def test_read_rejects_ambiguous_or_invalid_json(tmp_path: Path, content: bytes) -> None:
    store = _store(tmp_path)
    path = store.path_for(_key())
    path.parent.mkdir(parents=True)
    path.write_bytes(content)

    with pytest.raises(StateCorruptionError):
        store.read(_key())


def test_read_rejects_document_over_limit_without_treating_it_as_missing(tmp_path: Path) -> None:
    store = _store(tmp_path, max_document_bytes=128)
    path = store.path_for(_key())
    path.parent.mkdir(parents=True)
    path.write_bytes(b'x' * 1024)

    with pytest.raises(StateTooLargeError):
        store.read(_key())


def test_read_rejects_excessive_json_depth_as_corruption(tmp_path: Path) -> None:
    store = _store(tmp_path)
    nested: dict[str, Any] = {}
    cursor = nested
    for _ in range(40):
        child: dict[str, Any] = {}
        cursor['nested'] = child
        cursor = child
    payload = {
        'schema_version': 1,
        'updated_at_utc': '2026-07-20T15:00:00Z',
        'value': nested,
    }
    path = store.path_for(_key())
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding='utf-8')

    with pytest.raises(StateCorruptionError):
        store.read(_key())


def test_failed_replace_preserves_last_committed_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.replace(_key(), {'change_token': 'committed'})

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError('simulated replacement failure')

    monkeypatch.setattr(store_module.os, 'replace', fail_replace)

    with pytest.raises(StateWriteError):
        store.replace(_key(), {'change_token': 'uncommitted'})

    payload = json.loads(store.path_for(_key()).read_bytes())
    assert payload['value'] == {'change_token': 'committed'}
    assert list(store.path_for(_key()).parent.glob('*.tmp')) == []


def test_document_limit_applies_before_replacing_current_state(tmp_path: Path) -> None:
    store = _store(tmp_path, max_document_bytes=180)
    store.replace(_key(), {'change_token': 'small'})

    with pytest.raises(StateTooLargeError):
        store.replace(_key(), {'payload': 'x' * 500})

    assert store.read(_key()).value == {'change_token': 'small'}


def test_next_replace_removes_only_owned_orphan_temporaries(tmp_path: Path) -> None:
    logger = CapturingLogger()
    store = _store(tmp_path, logger=logger)
    path = store.path_for(_key())
    path.parent.mkdir(parents=True)
    owned_orphan = path.with_name(f'.{path.name}.{"a" * 32}.tmp')
    unrelated_temporary = path.with_name(f'.{path.name}.manual.tmp')
    owned_orphan.write_text('incomplete state', encoding='utf-8')
    unrelated_temporary.write_text('must remain', encoding='utf-8')

    store.replace(_key(), {'change_token': 'recovered'})

    assert not owned_orphan.exists()
    assert unrelated_temporary.read_text(encoding='utf-8') == 'must remain'
    recovery_events = [
        event for event in logger.events if event['event_name'] == 'state.temporary.recovered'
    ]
    assert len(recovery_events) == 1
    assert recovery_events[0]['metrics'] == {'removed_count': 1}


def test_orphan_cleanup_failure_preserves_committed_document(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.replace(_key(), {'change_token': 'committed'})
    path = store.path_for(_key())
    orphan_directory = path.with_name(f'.{path.name}.{"b" * 32}.tmp')
    orphan_directory.mkdir()

    with pytest.raises(StateWriteError):
        store.replace(_key(), {'change_token': 'must-not-commit'})

    assert store.read(_key()).value == {'change_token': 'committed'}


def test_observability_never_receives_state_values(tmp_path: Path) -> None:
    logger = CapturingLogger()
    store = _store(tmp_path, logger=logger)

    store.replace(_key(), {'token': 'secret-message-id'})
    store.read(_key())
    with pytest.raises(StateValidationError):
        store.replace(_key(), {'secret-dynamic-key': object()})

    serialized_events = json.dumps(logger.events, default=str)
    assert 'secret-message-id' not in serialized_events
    assert 'secret-dynamic-key' not in serialized_events
    assert {event['event_name'] for event in logger.events} == {
        'state.write.succeeded',
        'state.read.succeeded',
        'state.write.failed',
    }


def test_observability_failure_never_changes_storage_result(tmp_path: Path) -> None:
    class FailingLogger:
        def log(self, *_: Any, **__: Any) -> bool:
            raise RuntimeError('telemetry unavailable')

    store = _store(tmp_path, logger=FailingLogger())

    assert store.read(_key()) is None
    written = store.replace(_key(), {'change_token': 'committed'})
    assert store.read(_key()) == written


def test_store_rejects_invalid_direct_contracts(tmp_path: Path) -> None:
    with pytest.raises(StateValidationError, match='absolute'):
        AtomicStateStore(volume_path='relative', application='ada')
    with pytest.raises(StateValidationError, match='clock'):
        AtomicStateStore(volume_path=tmp_path, application='ada', clock='invalid')
    with pytest.raises(StateValidationError, match='logger'):
        AtomicStateStore(volume_path=tmp_path, application='ada', logger=object())

    store = _store(tmp_path)
    with pytest.raises(StateValidationError, match='StateKey'):
        store.read('invalid')
    with pytest.raises(StateValidationError, match='StateKey'):
        store.replace('invalid', {})


def test_concurrent_readers_observe_only_complete_documents(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.replace(_key(), {'revision': 0, 'payload': 'x' * 500})

    def read_revisions() -> list[int]:
        return [store.read(_key()).value['revision'] for _ in range(30)]

    with ThreadPoolExecutor(max_workers=5) as executor:
        readers = [executor.submit(read_revisions) for _ in range(4)]
        for revision in range(1, 31):
            store.replace(_key(), {'revision': revision, 'payload': 'x' * 500})

    revisions = [revision for reader in readers for revision in reader.result()]
    assert all(isinstance(revision, int) for revision in revisions)


def test_warning_quality_is_a_valid_committed_state(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.replace(
        _key(),
        {
            'change_token': 'new-publication',
            'quality_status': 'warning',
            'missing_count': 3,
        },
    )

    assert store.read(_key()).value == {
        'change_token': 'new-publication',
        'quality_status': 'warning',
        'missing_count': 3,
    }



def test_replace_serializes_clock_and_commit_order_between_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timestamps = iter(
        (
            datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 16, 0, tzinfo=UTC),
        )
    )
    store = AtomicStateStore(
        volume_path=tmp_path,
        application='ada',
        clock=lambda: next(timestamps),
    )
    original_replace_bytes = store._replace_bytes
    first_waiting = Event()
    second_committed = Event()

    def coordinated_replace_bytes(path: Path, content: bytes) -> int:
        if b'"revision":"older"' in content:
            first_waiting.set()
            second_committed.wait(timeout=0.2)
            return original_replace_bytes(path, content)
        result = original_replace_bytes(path, content)
        second_committed.set()
        return result

    monkeypatch.setattr(store, '_replace_bytes', coordinated_replace_bytes)

    with ThreadPoolExecutor(max_workers=2) as executor:
        older = executor.submit(store.replace, _key(), {'revision': 'older'})
        assert first_waiting.wait(timeout=1)
        newer = executor.submit(store.replace, _key(), {'revision': 'newer'})
        older.result(timeout=2)
        newer.result(timeout=2)

    committed = store.read(_key())
    assert committed is not None
    assert committed.updated_at_utc == datetime(2026, 7, 20, 16, 0, tzinfo=UTC)
    assert committed.value == {'revision': 'newer'}


def test_replace_fsyncs_parent_directory_after_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    replaced = Event()
    original_replace = store_module.os.replace
    synced_directories: list[Path] = []

    def tracked_replace(source: Path, target: Path) -> None:
        original_replace(source, target)
        replaced.set()

    def tracked_directory_fsync(path: Path) -> None:
        assert replaced.is_set()
        synced_directories.append(path)

    monkeypatch.setattr(store_module.os, 'replace', tracked_replace)
    monkeypatch.setattr(store_module, '_fsync_directory', tracked_directory_fsync)

    store.replace(_key(), {'change_token': 'durable'})

    assert synced_directories == [store.path_for(_key()).parent]
