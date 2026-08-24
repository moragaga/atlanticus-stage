from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from queue import Queue
from typing import Iterator

from ada.alarms.persistence import AlarmPersistence
from tests.support import build_record


class _GenerationFence:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._generation = 1

    def takeover(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    def authority(self, generation: int) -> Callable[[], None]:
        def check() -> None:
            with self._lock:
                if generation != self._generation:
                    raise RuntimeError('lease lost')

        return check

    def mutation(
        self,
        generation: int,
        *,
        pause_on_call: int | None = None,
        paused: threading.Event | None = None,
        resume: threading.Event | None = None,
    ) -> Callable[[], AbstractContextManager[None]]:
        calls = 0

        @contextmanager
        def enter() -> Iterator[None]:
            nonlocal calls
            calls += 1
            if pause_on_call == calls:
                if paused is None or resume is None:
                    raise AssertionError('pause events are required')
                paused.set()
                if not resume.wait(timeout=5):
                    raise AssertionError('timed out waiting to resume fenced mutation')
            with self._lock:
                if generation != self._generation:
                    raise RuntimeError('lease lost')
                yield

        return enter


def _run_commit(
    persistence: AlarmPersistence,
    *,
    authority: Callable[[], None],
    mutation: Callable[[], AbstractContextManager[None]],
    errors: Queue[BaseException],
) -> None:
    try:
        persistence.commit_batch(
            [build_record()],
            assert_authority=authority,
            fenced_mutation=mutation,
        )
    except Exception as error:
        errors.put(error)


def test_takeover_after_wal_append_blocks_stale_durable_publication(tmp_path: Path) -> None:
    first = AlarmPersistence(shared_volume_path=tmp_path)
    second = AlarmPersistence(shared_volume_path=tmp_path)
    coordinator = _GenerationFence()
    paused = threading.Event()
    resume = threading.Event()
    errors: Queue[BaseException] = Queue()
    thread = threading.Thread(
        target=_run_commit,
        kwargs={
            'persistence': first,
            'authority': coordinator.authority(1),
            'mutation': coordinator.mutation(
                1,
                pause_on_call=2,
                paused=paused,
                resume=resume,
            ),
            'errors': errors,
        },
        daemon=True,
    )

    thread.start()
    assert paused.wait(timeout=5)
    assert first.read_head().durable is None
    open_files = list(first.paths.journal_open_root.rglob('*.jsonl'))
    assert len(open_files) == 1
    assert open_files[0].stat().st_size > 0

    assert coordinator.takeover() == 2
    resume.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    error = errors.get_nowait()
    assert isinstance(error, RuntimeError)
    assert str(error) == 'lease lost'
    assert first.read_head().durable is None

    recovery = second.recover(
        assert_authority=coordinator.authority(2),
        fenced_mutation=coordinator.mutation(2),
    )

    assert recovery.discarded_tail_bytes > 0
    assert second.read_head().durable is None
    assert not list(second.paths.journal_open_root.rglob('*.jsonl'))


def test_takeover_after_durable_publish_blocks_stale_snapshot_and_replays_exact_commit(
    tmp_path: Path,
) -> None:
    first = AlarmPersistence(shared_volume_path=tmp_path)
    second = AlarmPersistence(shared_volume_path=tmp_path)
    coordinator = _GenerationFence()
    paused = threading.Event()
    resume = threading.Event()
    errors: Queue[BaseException] = Queue()
    record = build_record()
    thread = threading.Thread(
        target=_run_commit,
        kwargs={
            'persistence': first,
            'authority': coordinator.authority(1),
            'mutation': coordinator.mutation(
                1,
                pause_on_call=3,
                paused=paused,
                resume=resume,
            ),
            'errors': errors,
        },
        daemon=True,
    )

    thread.start()
    assert paused.wait(timeout=5)
    head = first.read_head()
    assert head.durable is not None
    assert head.materialized is None
    assert first.read_snapshot('crusher_pressure') is None

    assert coordinator.takeover() == 2
    resume.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    error = errors.get_nowait()
    assert isinstance(error, RuntimeError)
    assert str(error) == 'lease lost'
    assert first.read_snapshot('crusher_pressure') is None

    recovery = second.recover(
        assert_authority=coordinator.authority(2),
        fenced_mutation=coordinator.mutation(2),
    )

    assert recovery.applied_count == 1
    assert recovery.skipped_count == 0
    assert second.read_snapshot('crusher_pressure') == record.snapshot_after
    assert second.read_head().aligned
