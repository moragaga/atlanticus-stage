from datetime import UTC, datetime, timedelta

from atlanticus.data_producers.fabrica import FabricaProducerState, FabricaSourceBlob
from atlanticus.state import AtomicStateStore


def _blob(*, minute: int, etag: str) -> FabricaSourceBlob:
    value = datetime(2026, 8, 10, 17, minute, tzinfo=UTC)
    return FabricaSourceBlob(
        name=f'planes_fabrica_2026081017{minute:02d}00.parquet',
        source_file_timestamp_utc=value,
        size=100,
        etag=etag,
        last_modified_utc=value,
    )


def test_new_source_can_advance_watermark_without_advancing_revision(tmp_path) -> None:
    now = datetime(2026, 8, 10, 18, 0, tzinfo=UTC)
    state = FabricaProducerState(
        store=AtomicStateStore(volume_path=tmp_path, application='ada'), clock=lambda: now
    )
    first = state.commit_stream(
        stream_key='planes',
        source_blob=_blob(minute=10, etag='a'),
        catalog_signature='catalog',
        changed=True,
        publication_signatures={'day': 'sha256:a'},
    )
    assert first.revision == 1
    now += timedelta(minutes=1)
    second = state.commit_stream(
        stream_key='planes',
        source_blob=_blob(minute=20, etag='b'),
        catalog_signature='catalog',
        changed=False,
        publication_signatures={'day': 'sha256:a'},
    )
    assert second.revision == 1
    assert second.streams['planes'].revision == 1
    assert (
        second.streams['planes'].source_watermark_utc
        == _blob(minute=20, etag='b').source_file_timestamp_utc
    )


def test_source_identity_includes_catalog_and_remote_identity(tmp_path) -> None:
    state = FabricaProducerState(store=AtomicStateStore(volume_path=tmp_path, application='ada'))
    blob = _blob(minute=10, etag='a')
    state.commit_stream(
        stream_key='planes',
        source_blob=blob,
        catalog_signature='catalog-a',
        changed=True,
        publication_signatures={'day': 'sha256:a'},
    )
    assert state.source_is_current(
        stream_key='planes', source_blob=blob, catalog_signature='catalog-a'
    )
    assert not state.source_is_current(
        stream_key='planes', source_blob=blob, catalog_signature='catalog-b'
    )
