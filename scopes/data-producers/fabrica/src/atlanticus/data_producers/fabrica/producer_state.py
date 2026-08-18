from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType

from atlanticus.data_producers.fabrica.models import FabricaSourceBlob
from atlanticus.state import AtomicStateStore, StateKey


@dataclass(frozen=True, slots=True)
class FabricaStreamState:
    revision: int = 0
    source_watermark_utc: datetime | None = None
    last_change_at_utc: datetime | None = None
    last_attempt_at_utc: datetime | None = None
    last_success_at_utc: datetime | None = None
    source_blob_name: str | None = None
    source_blob_etag: str | None = None
    source_blob_size: int | None = None
    source_blob_last_modified_utc: datetime | None = None
    catalog_signature: str | None = None
    publication_signatures: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class FabricaProducerManifest:
    revision: int
    last_change_at_utc: datetime | None
    streams: Mapping[str, FabricaStreamState]


class FabricaProducerState:
    def __init__(
        self,
        *,
        store: AtomicStateStore,
        clock: Callable[[], datetime] | None = None,
        producer_key: str = 'fabrica',
    ) -> None:
        if not isinstance(store, AtomicStateStore):
            raise TypeError('store must be an AtomicStateStore')
        self._store = store
        self._producer_key = _stream_key(producer_key)
        self._state_key = StateKey(namespace=('producers',), name=self._producer_key)
        self._clock = clock or _utc_now
        self._current: FabricaProducerManifest | None = None

    def current(self) -> FabricaProducerManifest:
        if self._current is None:
            document = self._store.read(self._state_key)
            self._current = (
                _empty_manifest()
                if document is None
                else _decode(document.value, producer_key=self._producer_key)
            )
        return self._current

    def stream(self, key: str) -> FabricaStreamState:
        return self.current().streams.get(_stream_key(key), FabricaStreamState())

    def source_is_current(
        self,
        *,
        stream_key: str,
        source_blob: FabricaSourceBlob,
        catalog_signature: str,
    ) -> bool:
        state = self.stream(stream_key)
        same_remote_identity = False
        if source_blob.etag is not None:
            same_remote_identity = state.source_blob_etag == source_blob.etag
        elif source_blob.last_modified_utc is not None:
            same_remote_identity = (
                state.source_blob_last_modified_utc == source_blob.last_modified_utc
            )
        return (
            state.source_blob_name == source_blob.name
            and state.source_blob_size == source_blob.size
            and same_remote_identity
            and state.catalog_signature == catalog_signature
        )

    def mark_attempt(self, stream_key: str) -> None:
        key = _stream_key(stream_key)
        current = self.current()
        previous = current.streams.get(key, FabricaStreamState())
        streams = dict(current.streams)
        streams[key] = _copy_state(previous, last_attempt_at_utc=_normalize_utc(self._clock()))
        self._persist(
            FabricaProducerManifest(
                revision=current.revision,
                last_change_at_utc=current.last_change_at_utc,
                streams=MappingProxyType(streams),
            )
        )

    def commit_stream(
        self,
        *,
        stream_key: str,
        source_blob: FabricaSourceBlob,
        catalog_signature: str,
        changed: bool,
        publication_signatures: Mapping[str, str],
    ) -> FabricaProducerManifest:
        key = _stream_key(stream_key)
        current = self.current()
        previous = current.streams.get(key, FabricaStreamState())
        signatures = MappingProxyType(dict(publication_signatures))
        recovered = any(
            previous.publication_signatures.get(name) != value for name, value in signatures.items()
        )
        effective_change = changed or recovered
        now = _normalize_utc(self._clock())
        next_signatures = dict(previous.publication_signatures)
        next_signatures.update(signatures)
        updated = FabricaStreamState(
            revision=previous.revision + (1 if effective_change else 0),
            source_watermark_utc=max(
                filter(
                    lambda item: item is not None,
                    (previous.source_watermark_utc, source_blob.source_file_timestamp_utc),
                ),
                default=None,
            ),
            last_change_at_utc=now if effective_change else previous.last_change_at_utc,
            last_attempt_at_utc=previous.last_attempt_at_utc,
            last_success_at_utc=now,
            source_blob_name=source_blob.name,
            source_blob_etag=source_blob.etag,
            source_blob_size=source_blob.size,
            source_blob_last_modified_utc=source_blob.last_modified_utc,
            catalog_signature=catalog_signature,
            publication_signatures=MappingProxyType(next_signatures),
        )
        streams = dict(current.streams)
        streams[key] = updated
        manifest = FabricaProducerManifest(
            revision=current.revision + (1 if effective_change else 0),
            last_change_at_utc=now if effective_change else current.last_change_at_utc,
            streams=MappingProxyType(streams),
        )
        self._persist(manifest)
        return manifest

    def _persist(self, manifest: FabricaProducerManifest) -> None:
        self._store.replace(self._state_key, _encode(manifest, producer_key=self._producer_key))
        self._current = manifest


def _empty_manifest() -> FabricaProducerManifest:
    return FabricaProducerManifest(0, None, MappingProxyType({}))


def _copy_state(value: FabricaStreamState, **changes: object) -> FabricaStreamState:
    data = {
        field_name: getattr(value, field_name)
        for field_name in FabricaStreamState.__dataclass_fields__
    }
    data.update(changes)
    return FabricaStreamState(**data)


def _encode(value: FabricaProducerManifest, *, producer_key: str) -> dict[str, object]:
    return {
        'producer': producer_key,
        'revision': value.revision,
        'last_change_at_utc': _iso(value.last_change_at_utc),
        'streams': {
            key: {
                'revision': state.revision,
                'source_watermark_utc': _iso(state.source_watermark_utc),
                'last_change_at_utc': _iso(state.last_change_at_utc),
                'last_attempt_at_utc': _iso(state.last_attempt_at_utc),
                'last_success_at_utc': _iso(state.last_success_at_utc),
                'source_blob_name': state.source_blob_name,
                'source_blob_etag': state.source_blob_etag,
                'source_blob_size': state.source_blob_size,
                'source_blob_last_modified_utc': _iso(state.source_blob_last_modified_utc),
                'catalog_signature': state.catalog_signature,
                'publication_signatures': dict(state.publication_signatures),
            }
            for key, state in value.streams.items()
        },
    }


def _decode(value: Mapping[str, object], *, producer_key: str) -> FabricaProducerManifest:
    if value.get('producer') != producer_key:
        raise ValueError('Fabrica producer state has an invalid producer')
    raw_streams = value.get('streams')
    if not isinstance(raw_streams, Mapping):
        raise ValueError('Fabrica producer state streams must be a mapping')
    streams: dict[str, FabricaStreamState] = {}
    for key, raw in raw_streams.items():
        if not isinstance(raw, Mapping):
            raise ValueError('Fabrica stream state must be a mapping')
        signatures = raw.get('publication_signatures', {})
        if not isinstance(signatures, Mapping):
            raise ValueError('publication_signatures must be a mapping')
        streams[_stream_key(str(key))] = FabricaStreamState(
            revision=int(raw.get('revision', 0)),
            source_watermark_utc=_datetime(raw.get('source_watermark_utc')),
            last_change_at_utc=_datetime(raw.get('last_change_at_utc')),
            last_attempt_at_utc=_datetime(raw.get('last_attempt_at_utc')),
            last_success_at_utc=_datetime(raw.get('last_success_at_utc')),
            source_blob_name=_optional_text(raw.get('source_blob_name')),
            source_blob_etag=_optional_text(raw.get('source_blob_etag')),
            source_blob_size=(
                None if raw.get('source_blob_size') is None else int(raw['source_blob_size'])
            ),
            source_blob_last_modified_utc=_datetime(raw.get('source_blob_last_modified_utc')),
            catalog_signature=_optional_text(raw.get('catalog_signature')),
            publication_signatures=MappingProxyType(
                {str(name): str(signature) for name, signature in signatures.items()}
            ),
        )
    return FabricaProducerManifest(
        revision=int(value.get('revision', 0)),
        last_change_at_utc=_datetime(value.get('last_change_at_utc')),
        streams=MappingProxyType(streams),
    )


def _stream_key(value: str) -> str:
    normalized = str(value).strip().lower()
    if not normalized:
        raise ValueError('stream_key is required')
    return normalized


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _datetime(value: object) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value))
    return _normalize_utc(parsed)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else _normalize_utc(value).isoformat()


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)
