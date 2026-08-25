import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ada.processes.alarms_runtime import (
    RUNTIME_MANIFEST_SCHEMA_VERSION,
    FileRuntimeRevisionCache,
    FileRuntimeRevisionSource,
    RuntimeManifest,
    RuntimeRevisionBundle,
    RuntimeRevisionCacheError,
    RuntimeRevisionSourceError,
)
from atlanticus.state import AtomicJsonStore, StateWriteError

PUBLISHED_AT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _manifest() -> RuntimeManifest:
    return RuntimeManifest(
        schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
        alarm_configuration_revision='AC-52',
        tool_registry_revision='TR-18',
        published_at=PUBLISHED_AT,
    )


def _manifest_document() -> dict[str, object]:
    return {
        'schema_version': RUNTIME_MANIFEST_SCHEMA_VERSION,
        'alarm_configuration_revision': 'AC-52',
        'tool_registry_revision': 'TR-18',
        'published_at': '2026-08-25T12:00:00Z',
    }


def _bundle() -> RuntimeRevisionBundle:
    return RuntimeRevisionBundle(
        manifest=_manifest(),
        alarm_configuration={'revision': 'AC-52', 'alarms': []},
        tool_registry={'revision': 'TR-18', 'tools': []},
    )


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding='utf-8')


def test_file_source_reads_manifest_and_exact_revision_documents(tmp_path: Path) -> None:
    _write_json(tmp_path / 'runtime-manifest.json', _manifest_document())
    _write_json(tmp_path / 'alarm-configuration' / 'AC-52.json', {'revision': 'AC-52'})
    _write_json(tmp_path / 'alarm-configuration' / 'AC-51.json', {'revision': 'AC-51'})
    _write_json(tmp_path / 'tool-registry' / 'TR-18.json', {'revision': 'TR-18'})
    source = FileRuntimeRevisionSource(root_path=tmp_path)

    manifest = source.read_manifest()
    alarm_document = source.read_alarm_configuration(revision='AC-52')
    tool_document = source.read_tool_registry(revision='TR-18')

    assert manifest.revision_key == ('AC-52', 'TR-18')
    assert alarm_document == {'revision': 'AC-52'}
    assert tool_document == {'revision': 'TR-18'}


@pytest.mark.parametrize('revision', ['../AC-52', 'nested/AC-52', r'nested\AC-52', '.', '..'])
def test_file_source_rejects_revision_path_traversal(tmp_path: Path, revision: str) -> None:
    source = FileRuntimeRevisionSource(root_path=tmp_path)

    with pytest.raises(RuntimeRevisionSourceError, match='safe file name'):
        source.read_alarm_configuration(revision=revision)


def test_file_source_reports_missing_or_corrupt_manifest(tmp_path: Path) -> None:
    source = FileRuntimeRevisionSource(root_path=tmp_path)

    with pytest.raises(RuntimeRevisionSourceError, match='does not exist'):
        source.read_manifest()

    _write_json(tmp_path / 'runtime-manifest.json', {'schema_version': 'invalid'})
    with pytest.raises(RuntimeRevisionSourceError, match='manifest is invalid'):
        source.read_manifest()


def test_file_source_reports_missing_exact_revision(tmp_path: Path) -> None:
    source = FileRuntimeRevisionSource(root_path=tmp_path)

    with pytest.raises(RuntimeRevisionSourceError, match='does not exist'):
        source.read_tool_registry(revision='TR-18')


def test_file_cache_empty_state_requires_all_three_documents_to_be_missing(tmp_path: Path) -> None:
    cache = FileRuntimeRevisionCache(root_path=tmp_path)

    assert cache.load_effective() is None

    AtomicJsonStore(root_path=tmp_path).replace(
        'runtime/cache/alarm-configuration.json',
        {'revision': 'AC-52'},
    )
    with pytest.raises(RuntimeRevisionCacheError, match='incomplete'):
        cache.load_effective()


def test_file_cache_round_trip_preserves_effective_bundle(tmp_path: Path) -> None:
    cache = FileRuntimeRevisionCache(root_path=tmp_path)
    bundle = _bundle()

    cache.replace_effective(bundle=bundle)
    loaded = cache.load_effective()

    assert loaded is not None
    assert loaded.manifest == bundle.manifest
    assert loaded.alarm_configuration == bundle.alarm_configuration
    assert loaded.tool_registry == bundle.tool_registry
    assert (tmp_path / 'runtime/cache/runtime-manifest.json').exists()
    assert (tmp_path / 'runtime/cache/alarm-configuration.json').exists()
    assert (tmp_path / 'runtime/cache/tool-registry.json').exists()


def test_file_cache_writes_manifest_last_as_local_commit_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = FileRuntimeRevisionCache(root_path=tmp_path)
    calls: list[str] = []
    original_replace = cache._store.replace

    def tracked_replace(relative_path, value):
        calls.append(str(relative_path))
        return original_replace(relative_path, value)

    monkeypatch.setattr(cache._store, 'replace', tracked_replace)

    cache.replace_effective(bundle=_bundle())

    assert calls == [
        'runtime/cache/alarm-configuration.json',
        'runtime/cache/tool-registry.json',
        'runtime/cache/runtime-manifest.json',
    ]


def test_file_cache_rejects_corrupt_manifest(tmp_path: Path) -> None:
    store = AtomicJsonStore(root_path=tmp_path)
    store.replace('runtime/cache/runtime-manifest.json', {'schema_version': 'invalid'})
    store.replace('runtime/cache/alarm-configuration.json', {'revision': 'AC-52'})
    store.replace('runtime/cache/tool-registry.json', {'revision': 'TR-18'})

    with pytest.raises(RuntimeRevisionCacheError, match='cache is invalid'):
        FileRuntimeRevisionCache(root_path=tmp_path).load_effective()


def test_file_cache_requires_runtime_revision_bundle(tmp_path: Path) -> None:
    cache = FileRuntimeRevisionCache(root_path=tmp_path)

    with pytest.raises(TypeError, match='RuntimeRevisionBundle'):
        cache.replace_effective(bundle=object())


def test_file_cache_failed_replacement_keeps_previous_manifest_commit_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = FileRuntimeRevisionCache(root_path=tmp_path)
    previous = _bundle()
    cache.replace_effective(bundle=previous)
    target = RuntimeRevisionBundle(
        manifest=RuntimeManifest(
            schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
            alarm_configuration_revision='AC-53',
            tool_registry_revision='TR-19',
            published_at=datetime(2026, 8, 25, 13, 0, tzinfo=UTC),
        ),
        alarm_configuration={'revision': 'AC-53'},
        tool_registry={'revision': 'TR-19'},
    )
    original_replace = cache._store.replace

    def fail_registry_replace(relative_path, value):
        if str(relative_path) == 'runtime/cache/tool-registry.json':
            raise StateWriteError('simulated registry write failure')
        return original_replace(relative_path, value)

    monkeypatch.setattr(cache._store, 'replace', fail_registry_replace)

    with pytest.raises(RuntimeRevisionCacheError, match='could not replace'):
        cache.replace_effective(bundle=target)

    manifest_document = AtomicJsonStore(root_path=tmp_path).read(
        'runtime/cache/runtime-manifest.json'
    )
    assert manifest_document is not None
    assert manifest_document['alarm_configuration_revision'] == 'AC-52'
    assert manifest_document['tool_registry_revision'] == 'TR-18'
