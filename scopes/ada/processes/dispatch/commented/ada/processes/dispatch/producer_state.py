# Persiste progreso independiente por fuente, firmas de publicación y última sincronización.
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

from atlanticus.connectivity.sql.models import SqlTableChangeMarker
from atlanticus.state.models import StateKey
from atlanticus.state.store import AtomicStateStore

_STATE_NAMESPACE = ('producers', 'dispatch')


@dataclass(frozen=True, slots=True)
class DispatchSourceState:
    source_key: str
    revision: int = 0
    source_change_marker: SqlTableChangeMarker | None = None
    source_scope_token: str | None = None
    source_last_update_utc: datetime | None = None
    last_synced_at_utc: datetime | None = None
    last_change_at_utc: datetime | None = None
    publication_signatures: Mapping[str, str] = MappingProxyType({})


class DispatchProducerState:
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
        self._cache: dict[str, DispatchSourceState] = {}

    def source_state(self, source_key: str) -> DispatchSourceState:
        normalized_key = _source_key(source_key)
        cached = self._cache.get(normalized_key)
        if cached is not None:
            return cached
        document = self._store.read(_state_key(normalized_key))
        state = (
            DispatchSourceState(source_key=normalized_key)
            if document is None
            else _state_from_value(normalized_key, document.value)
        )
        self._cache[normalized_key] = state
        return state

    def commit_source(
        self,
        *,
        source_key: str,
        target_change_marker: SqlTableChangeMarker,
        target_scope_token: str | None,
        changed: bool,
        source_last_update_utc: datetime | None,
        publication_signatures: Mapping[str, str],
    ) -> DispatchSourceState:
        normalized_key = _source_key(source_key)
        if not isinstance(target_change_marker, SqlTableChangeMarker):
            raise TypeError('target_change_marker must be a SqlTableChangeMarker')
        if not isinstance(changed, bool):
            raise ValueError('changed must be a boolean')
        previous = self.source_state(normalized_key)
        now = _normalize_utc(self._clock())
        normalized_signatures = _signatures(publication_signatures)
        recovered_change = any(
            previous.publication_signatures.get(target) != signature
            for target, signature in normalized_signatures.items()
        )
        effective_change = changed or recovered_change
        next_state = DispatchSourceState(
            source_key=normalized_key,
            revision=previous.revision + (1 if effective_change else 0),
            source_change_marker=target_change_marker,
            source_scope_token=_optional_text(target_scope_token),
            source_last_update_utc=_max_datetime(
                previous.source_last_update_utc,
                source_last_update_utc,
            ),
            last_synced_at_utc=now,
            last_change_at_utc=now if effective_change else previous.last_change_at_utc,
            publication_signatures=MappingProxyType(
                normalized_signatures or dict(previous.publication_signatures)
            ),
        )
        self._store.replace(_state_key(normalized_key), _state_value(next_state))
        self._cache[normalized_key] = next_state
        return next_state


def marker_changed(
    previous: SqlTableChangeMarker | None,
    current: SqlTableChangeMarker,
) -> bool:
    if not isinstance(current, SqlTableChangeMarker):
        raise TypeError('current must be a SqlTableChangeMarker')
    if previous is None:
        return True
    if previous.source_table.lower() != current.source_table.lower():
        return True
    return (
        previous.generation_token != current.generation_token
        or previous.last_user_update_token != current.last_user_update_token
        or previous.user_updates != current.user_updates
    )


def _state_key(source_key: str) -> StateKey:
    return StateKey(namespace=_STATE_NAMESPACE, name=source_key)


def _state_from_value(source_key: str, value: Mapping[str, object]) -> DispatchSourceState:
    if value.get('producer') != 'dispatch':
        raise ValueError('Dispatch producer state has an invalid producer')
    if value.get('source_key') != source_key:
        raise ValueError('Dispatch producer state source_key does not match its state path')
    return DispatchSourceState(
        source_key=source_key,
        revision=_revision(value.get('revision')),
        source_change_marker=_optional_marker(value.get('source_change_marker')),
        source_scope_token=_optional_text(value.get('source_scope_token')),
        source_last_update_utc=_optional_datetime(value.get('source_last_update_utc')),
        last_synced_at_utc=_optional_datetime(value.get('last_synced_at_utc')),
        last_change_at_utc=_optional_datetime(value.get('last_change_at_utc')),
        publication_signatures=MappingProxyType(
            _signatures(value.get('publication_signatures', {}))
        ),
    )


def _state_value(state: DispatchSourceState) -> dict[str, object]:
    return {
        'producer': 'dispatch',
        'source_key': state.source_key,
        'revision': state.revision,
        'source_change_marker': _marker_value(state.source_change_marker),
        'source_scope_token': state.source_scope_token,
        'source_last_update_utc': _format_datetime(state.source_last_update_utc),
        'last_synced_at_utc': _format_datetime(state.last_synced_at_utc),
        'last_change_at_utc': _format_datetime(state.last_change_at_utc),
        'publication_signatures': dict(state.publication_signatures),
    }


def _marker_value(marker: SqlTableChangeMarker | None) -> dict[str, object] | None:
    if marker is None:
        return None
    return {
        'source_table': marker.source_table,
        'generation_token': marker.generation_token,
        'last_user_update_token': marker.last_user_update_token,
        'user_updates': marker.user_updates,
    }


def _optional_marker(value: object) -> SqlTableChangeMarker | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError('Dispatch producer state source_change_marker must be a mapping or null')
    return SqlTableChangeMarker(
        source_table=_required_text(value.get('source_table'), 'source_table'),
        generation_token=_required_text(value.get('generation_token'), 'generation_token'),
        last_user_update_token=_optional_text(value.get('last_user_update_token')),
        user_updates=_non_negative_integer(value.get('user_updates'), 'user_updates'),
    )


def _signatures(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError('publication_signatures must be a mapping')
    return {
        _required_text(key, 'publication signature target'): _required_text(
            signature, 'publication signature value'
        )
        for key, signature in value.items()
    }


def _source_key(value: object) -> str:
    return _required_text(value, 'source_key')


def _revision(value: object) -> int:
    return _non_negative_integer(value, 'revision')


def _non_negative_integer(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f'{field_name} must be a non-negative integer')
    return value


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('optional text values must be strings or None')
    normalized = value.strip()
    return normalized or None


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('Dispatch producer state timestamp must be a string or null')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('Dispatch producer state timestamp is invalid') from error
    return _normalize_utc(parsed)


def _normalize_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('Dispatch producer state timestamp must be timezone-aware')
    return value.astimezone(UTC)


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _max_datetime(first: datetime | None, second: datetime | None) -> datetime | None:
    values = tuple(value for value in (first, second) if value is not None)
    return max(values) if values else None


def _utc_now() -> datetime:
    return datetime.now(UTC)
