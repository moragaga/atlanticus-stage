from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

from atlanticus.integrations.pi.contracts import PiExtractionMode
from atlanticus.state import AtomicStateStore, StateKey

_STATE_KEY = StateKey(namespace=('producers',), name='notpii')


@dataclass(frozen=True, slots=True)
class NotPiiStreamObservation:
    source_last_updated_at_utc: datetime | None
    changed: bool

    def __post_init__(self) -> None:
        if (
            self.source_last_updated_at_utc is not None
            and self.source_last_updated_at_utc.tzinfo is None
        ):
            raise ValueError('source_last_updated_at_utc must be timezone-aware')
        if not isinstance(self.changed, bool):
            raise ValueError('changed must be a boolean')


@dataclass(frozen=True, slots=True)
class NotPiiStreamState:
    revision: int = 0
    source_watermark_utc: datetime | None = None
    last_change_at_utc: datetime | None = None


@dataclass(frozen=True, slots=True)
class NotPiiProducerManifest:
    revision: int
    source_watermark_utc: datetime | None
    last_change_at_utc: datetime | None
    streams: Mapping[PiExtractionMode, NotPiiStreamState]


class NotPiiProducerState:
    def __init__(
        self,
        *,
        store: AtomicStateStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(store, AtomicStateStore):
            raise TypeError('store must be an AtomicStateStore')
        self._store = store
        self._clock = clock or _utc_now
        self._current: NotPiiProducerManifest | None = None

    def current(self) -> NotPiiProducerManifest:
        if self._current is None:
            document = self._store.read(_STATE_KEY)
            self._current = (
                _empty_manifest() if document is None else _manifest_from_value(document.value)
            )
        return self._current

    def advance(
        self,
        observations: Mapping[PiExtractionMode, NotPiiStreamObservation],
    ) -> NotPiiProducerManifest:
        if not observations:
            return self.current()
        if any(not isinstance(mode, PiExtractionMode) for mode in observations):
            raise ValueError('observations keys must be PiExtractionMode values')
        if any(not isinstance(value, NotPiiStreamObservation) for value in observations.values()):
            raise ValueError('observations must contain NotPiiStreamObservation values')

        current = self.current()
        now = _normalize_utc(self._clock())
        streams = dict(current.streams)
        any_changed = False
        state_changed = False

        for mode, observation in observations.items():
            previous = streams.get(mode, NotPiiStreamState())
            next_watermark = _max_datetime(
                previous.source_watermark_utc,
                observation.source_last_updated_at_utc,
            )
            next_revision = previous.revision + (1 if observation.changed else 0)
            next_change_at = now if observation.changed else previous.last_change_at_utc
            updated = NotPiiStreamState(
                revision=next_revision,
                source_watermark_utc=next_watermark,
                last_change_at_utc=next_change_at,
            )
            streams[mode] = updated
            any_changed = any_changed or observation.changed
            state_changed = state_changed or updated != previous

        manifest = NotPiiProducerManifest(
            revision=current.revision + (1 if any_changed else 0),
            source_watermark_utc=_max_datetime(
                *(stream.source_watermark_utc for stream in streams.values())
            ),
            last_change_at_utc=now if any_changed else current.last_change_at_utc,
            streams=MappingProxyType(streams),
        )
        state_changed = state_changed or manifest.revision != current.revision
        state_changed = (
            state_changed or manifest.source_watermark_utc != current.source_watermark_utc
        )
        state_changed = state_changed or manifest.last_change_at_utc != current.last_change_at_utc
        if state_changed:
            self._store.replace(_STATE_KEY, _manifest_value(manifest))
        self._current = manifest
        return manifest


def _empty_manifest() -> NotPiiProducerManifest:
    return NotPiiProducerManifest(
        revision=0,
        source_watermark_utc=None,
        last_change_at_utc=None,
        streams=MappingProxyType({}),
    )


def _manifest_from_value(value: Mapping[str, object]) -> NotPiiProducerManifest:
    if value.get('producer') != 'notpii':
        raise ValueError('NOT PII producer state has an invalid producer')
    revision = _revision(value.get('revision'), field='revision')
    streams_value = value.get('streams')
    if not isinstance(streams_value, Mapping):
        raise ValueError('NOT PII producer state streams must be a mapping')
    streams: dict[PiExtractionMode, NotPiiStreamState] = {}
    for raw_mode, raw_state in streams_value.items():
        try:
            mode = PiExtractionMode(str(raw_mode))
        except ValueError as error:
            raise ValueError(
                'NOT PII producer state contains an invalid extraction mode'
            ) from error
        if not isinstance(raw_state, Mapping):
            raise ValueError('NOT PII producer stream state must be a mapping')
        streams[mode] = NotPiiStreamState(
            revision=_revision(raw_state.get('revision'), field='stream revision'),
            source_watermark_utc=_optional_datetime(raw_state.get('source_watermark_utc')),
            last_change_at_utc=_optional_datetime(raw_state.get('last_change_at_utc')),
        )
    return NotPiiProducerManifest(
        revision=revision,
        source_watermark_utc=_optional_datetime(value.get('source_watermark_utc')),
        last_change_at_utc=_optional_datetime(value.get('last_change_at_utc')),
        streams=MappingProxyType(streams),
    )


def _manifest_value(manifest: NotPiiProducerManifest) -> dict[str, object]:
    return {
        'producer': 'notpii',
        'revision': manifest.revision,
        'source_watermark_utc': _format_datetime(manifest.source_watermark_utc),
        'last_change_at_utc': _format_datetime(manifest.last_change_at_utc),
        'streams': {
            mode.value: {
                'revision': state.revision,
                'source_watermark_utc': _format_datetime(state.source_watermark_utc),
                'last_change_at_utc': _format_datetime(state.last_change_at_utc),
            }
            for mode, state in sorted(manifest.streams.items(), key=lambda item: item[0].value)
        },
    }


def _revision(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f'NOT PII producer state {field} must be a non-negative integer')
    return value


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('NOT PII producer state timestamp must be a string or null')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('NOT PII producer state timestamp is invalid') from error
    return _normalize_utc(parsed)


def _normalize_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('NOT PII producer state clock must return a timezone-aware datetime')
    return value.astimezone(UTC)


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _max_datetime(*values: datetime | None) -> datetime | None:
    present = tuple(value for value in values if value is not None)
    return max(present) if present else None


def _utc_now() -> datetime:
    return datetime.now(UTC)
