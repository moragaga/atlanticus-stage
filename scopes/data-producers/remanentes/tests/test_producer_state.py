from datetime import UTC, datetime, timedelta

from atlanticus.data_producers.remanentes import RemanentesSourceBlob
from atlanticus.data_producers.remanentes.producer_state import RemanentesProducerState
from atlanticus.state import AtomicStateStore


def _blob(*, minute: int, etag: str) -> RemanentesSourceBlob:
    value = datetime(2026, 8, 11, 0, minute, tzinfo=UTC)
    return RemanentesSourceBlob(
        name=f'remanentes/stocks/data_20260810_20{minute:02d}.parquet',
        source_file_timestamp_utc=value,
        size=100,
        etag=etag,
        last_modified_utc=value,
    )


def test_cursor_advances_without_revision_when_dataset_does_not_change(tmp_path) -> None:
    now = datetime(2026, 8, 11, 1, 0, tzinfo=UTC)
    state = RemanentesProducerState(
        store=AtomicStateStore(volume_path=tmp_path, application='ada'),
        clock=lambda: now,
    )
    first = state.commit_stream(
        stream_key='stocks',
        source_blob=_blob(minute=10, etag='a'),
        catalog_signature='catalog',
        changed=True,
        publication_signatures={'day': 'sha256:a'},
    )
    assert first.revision == 1
    now += timedelta(minutes=1)
    second = state.commit_stream(
        stream_key='stocks',
        source_blob=_blob(minute=20, etag='b'),
        catalog_signature='catalog',
        changed=False,
        publication_signatures={'day': 'sha256:a'},
    )
    assert second.revision == 1
    assert second.streams['stocks'].revision == 1
    assert second.streams['stocks'].source_blob_name.endswith('20260810_2020.parquet')


def test_publication_signature_recovers_change_after_state_gap(tmp_path) -> None:
    state = RemanentesProducerState(store=AtomicStateStore(volume_path=tmp_path, application='ada'))
    first = state.commit_stream(
        stream_key='stocks',
        source_blob=_blob(minute=10, etag='a'),
        catalog_signature='catalog',
        changed=False,
        publication_signatures={'day': 'sha256:a'},
    )
    assert first.revision == 1
