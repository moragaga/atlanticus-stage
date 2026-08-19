from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import atlanticus.json.store as store_module
from atlanticus.json import (
    JsonConflictError,
    JsonCorruptionError,
    JsonDocumentStore,
    JsonValidationError,
    JsonWriteError,
    JsonWriteOnceStatus,
)


def test_replace_and_read_round_trip(tmp_path: Path) -> None:
    store = JsonDocumentStore()
    path = tmp_path / 'datasets' / 'kpis' / 'latest' / 'data.json'

    store.replace(path, {'watermark_utc': '2026-08-19T18:15:10Z', 'value': 42.5})

    assert store.exists(path) is True
    assert store.read(path) == {
        'value': 42.5,
        'watermark_utc': '2026-08-19T18:15:10Z',
    }
    assert path.read_bytes().endswith(b'\n')


def test_missing_document_returns_none(tmp_path: Path) -> None:
    store = JsonDocumentStore()

    assert store.exists(tmp_path / 'missing.json') is False
    assert store.read(tmp_path / 'missing.json') is None


def test_write_once_is_idempotent_for_equivalent_content(tmp_path: Path) -> None:
    store = JsonDocumentStore()
    path = tmp_path / 'evaluation.json'

    first = store.write_once(path, {'b': 2, 'a': 1})
    second = store.write_once(path, {'a': 1, 'b': 2})

    assert first is JsonWriteOnceStatus.CREATED
    assert second is JsonWriteOnceStatus.UNCHANGED
    assert store.read(path) == {'a': 1, 'b': 2}


def test_write_once_rejects_different_content(tmp_path: Path) -> None:
    store = JsonDocumentStore()
    path = tmp_path / 'evaluation.json'
    store.write_once(path, {'value': 10})

    with pytest.raises(JsonConflictError, match='different content'):
        store.write_once(path, {'value': 11})

    assert store.read(path) == {'value': 10}


def test_corrupt_document_is_not_treated_as_missing(tmp_path: Path) -> None:
    store = JsonDocumentStore()
    path = tmp_path / 'corrupt.json'
    path.write_text('{not-json', encoding='utf-8')

    with pytest.raises(JsonCorruptionError):
        store.read(path)


def test_failed_replace_preserves_last_committed_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JsonDocumentStore()
    path = tmp_path / 'latest.json'
    store.replace(path, {'value': 'committed'})

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError('simulated replacement failure')

    monkeypatch.setattr(store_module.os, 'replace', fail_replace)

    with pytest.raises(JsonWriteError):
        store.replace(path, {'value': 'uncommitted'})

    assert json.loads(path.read_bytes()) == {'value': 'committed'}
    assert list(path.parent.glob('*.tmp')) == []


def test_concurrent_readers_observe_only_complete_replacements(tmp_path: Path) -> None:
    store = JsonDocumentStore()
    path = tmp_path / 'latest.json'
    store.replace(path, {'revision': 0, 'payload': 'x' * 500})

    def read_revisions() -> list[int]:
        revisions: list[int] = []
        for _ in range(50):
            document = store.read(path)
            assert document is not None
            revision = document['revision']
            assert isinstance(revision, int) and not isinstance(revision, bool)
            revisions.append(revision)
        return revisions

    with ThreadPoolExecutor(max_workers=5) as executor:
        readers = [executor.submit(read_revisions) for _ in range(4)]
        for revision in range(1, 31):
            store.replace(path, {'revision': revision, 'payload': 'x' * 500})

    revisions = [revision for reader in readers for revision in reader.result()]
    assert revisions
    assert all(0 <= revision <= 30 for revision in revisions)


def test_paths_must_be_absolute() -> None:
    store = JsonDocumentStore()

    with pytest.raises(JsonValidationError, match='absolute'):
        store.read('relative.json')
    with pytest.raises(JsonValidationError, match='absolute'):
        store.replace('relative.json', {})
    with pytest.raises(JsonValidationError, match='absolute'):
        store.write_once('relative.json', {})
