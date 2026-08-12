# La deduplicación guarda hashes con TTL y capacidad explícita, nunca una historia ilimitada.
"""Deduplicación temporal y acotada sobre el store atómico."""

from __future__ import annotations

import hashlib
import math
import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from atlanticus.observability import (
    EventAudience,
    EventSeverity,
    ObservabilityLogger,
    get_observability_logger,
)
from atlanticus.state.errors import StateCorruptionError, StateValidationError
from atlanticus.state.models import StateKey
from atlanticus.state.store import AtomicStateStore


class ExpiringKeySet:
    """Conserva hashes con TTL sin acumular identificadores históricos."""

    def __init__(
        self,
        *,
        store: AtomicStateStore,
        key: StateKey,
        retention_seconds: float,
        max_entries: int,
        clock: Callable[[], datetime] | None = None,
        logger: ObservabilityLogger | None = None,
    ) -> None:
        if not isinstance(store, AtomicStateStore):
            raise StateValidationError('store must be an AtomicStateStore')
        if not isinstance(key, StateKey):
            raise StateValidationError('key must be a StateKey')
        if (
            not isinstance(retention_seconds, int | float)
            or isinstance(retention_seconds, bool)
            or not math.isfinite(retention_seconds)
            or retention_seconds <= 0
        ):
            raise StateValidationError('retention_seconds must be a finite positive number')
        if not isinstance(max_entries, int) or isinstance(max_entries, bool) or max_entries <= 0:
            raise StateValidationError('max_entries must be greater than zero')
        resolved_clock = _utc_now if clock is None else clock
        if not callable(resolved_clock):
            raise StateValidationError('clock must be callable')
        resolved_logger = (
            get_observability_logger('atlanticus.state.expiring') if logger is None else logger
        )
        if not callable(getattr(resolved_logger, 'log', None)):
            raise StateValidationError('logger must provide a callable log method')
        self._store = store
        self._key = key
        self._retention_seconds = retention_seconds
        self._max_entries = max_entries
        self._clock = resolved_clock
        self._logger = resolved_logger
        self._lock = threading.RLock()

    def contains(self, raw_key: str) -> bool:
        """Comprueba una clave sin persistirla en texto claro."""

        return self.contains_many((raw_key,))[0]

    def contains_many(self, raw_keys: Iterable[str]) -> tuple[bool, ...]:
        """Resuelve un lote con una sola lectura y, si aplica, una sola purga."""

        values = _materialize_keys(raw_keys)
        if not values:
            return ()
        # El lote evita leer y reescribir el mismo JSON por cada mensaje de una entrega.
        with self._lock:
            now_epoch = self._now_epoch()
            entries = self._load_entries()
            removed = _purge_entries(entries, now_epoch)
            # La capacidad se vuelve a imponer en cada lectura para respetar cambios de configuración entre reinicios.
            evicted = _enforce_max_entries(entries, self._max_entries)
            if removed or evicted:
                self._store.replace(self._key, {'entries': entries})
            if removed:
                self._emit_cleanup('state.expiring_set.purged', removed, len(entries))
            if evicted:
                self._emit_eviction(evicted, len(entries))
            return tuple(_hash_key(value) in entries for value in values)

    def add(self, raw_key: str) -> int:
        """Agrega o renueva una clave y retorna el tamaño actual."""

        return self.add_many((raw_key,))

    def add_many(self, raw_keys: Iterable[str]) -> int:
        """Agrega un lote, purga expirados y limita el documento en una escritura."""

        values = _materialize_keys(raw_keys)
        if not values:
            return self.count()
        with self._lock:
            now_epoch = self._now_epoch()
            entries = self._load_entries()
            removed = _purge_entries(entries, now_epoch)
            expires_at = now_epoch + self._retention_seconds
            # La clave original nunca abandona la memoria de esta llamada.
            for value in values:
                entries[_hash_key(value)] = expires_at
            # TTL controla edad y max_entries controla volumen incluso ante una ráfaga inesperada.
            evicted = _enforce_max_entries(entries, self._max_entries)
            self._store.replace(self._key, {'entries': entries})
            if removed:
                self._emit_cleanup('state.expiring_set.purged', removed, len(entries))
            if evicted:
                self._emit_eviction(evicted, len(entries))
            return len(entries)

    def purge(self) -> int:
        """Elimina expirados; no crea un documento cuando la clave aún no existe."""

        with self._lock:
            now_epoch = self._now_epoch()
            document = self._store.read(self._key)
            if document is None:
                return 0
            entries = _parse_entries(document.value)
            removed = _purge_entries(entries, now_epoch)
            # Compactar también aplica la capacidad vigente, aunque no se agreguen claves nuevas.
            evicted = _enforce_max_entries(entries, self._max_entries)
            if removed or evicted:
                self._store.replace(self._key, {'entries': entries})
            if removed:
                self._emit_cleanup('state.expiring_set.purged', removed, len(entries))
            if evicted:
                self._emit_eviction(evicted, len(entries))
            return removed

    def count(self) -> int:
        """Retorna claves vigentes y compacta expirados durante la lectura."""

        with self._lock:
            now_epoch = self._now_epoch()
            entries = self._load_entries()
            removed = _purge_entries(entries, now_epoch)
            # count() refleja la capacidad configurada actual, no la de la ejecución que escribió el archivo.
            evicted = _enforce_max_entries(entries, self._max_entries)
            if removed or evicted:
                self._store.replace(self._key, {'entries': entries})
            if removed:
                self._emit_cleanup('state.expiring_set.purged', removed, len(entries))
            if evicted:
                self._emit_eviction(evicted, len(entries))
            return len(entries)

    def _load_entries(self) -> dict[str, float]:
        document = self._store.read(self._key)
        if document is None:
            return {}
        return _parse_entries(document.value)

    def _now_epoch(self) -> float:
        value = self._clock()
        if not isinstance(value, datetime):
            raise StateValidationError('expiring key set clock must return a datetime')
        if value.tzinfo is None:
            raise StateValidationError('expiring key set clock must be timezone-aware')
        return value.astimezone(UTC).timestamp()

    def _emit_cleanup(self, event_name: str, removed: int, current: int) -> None:
        self._safe_log(
            EventSeverity.INFO,
            'Expired state entries were removed.',
            event_name=event_name,
            audience=EventAudience.LOCAL,
            metrics={'removed_count': removed, 'entry_count': current},
            attributes={'state_key': self._key.identifier},
        )

    # La evicción es operacional porque una capacidad insuficiente afecta el horizonte efectivo de deduplicación.
    def _emit_eviction(self, evicted: int, current: int) -> None:
        self._safe_log(
            EventSeverity.WARNING,
            'Expiring key set reached its configured capacity.',
            event_name='state.expiring_set.evicted',
            audience=EventAudience.OPERATIONS,
            metrics={'evicted_count': evicted, 'entry_count': current},
            attributes={'state_key': self._key.identifier},
        )

    # El backend de observabilidad nunca participa del resultado funcional.
    def _safe_log(self, *args: Any, **kwargs: Any) -> None:
        try:
            self._logger.log(*args, **kwargs)
        except Exception:
            pass


def _materialize_keys(raw_keys: Iterable[str]) -> tuple[str, ...]:
    if isinstance(raw_keys, str | bytes):
        raise StateValidationError('raw_keys must be an iterable of strings, not a string')
    try:
        values = tuple(raw_keys)
    except TypeError as error:
        raise StateValidationError('raw_keys must be an iterable of strings') from error
    for value in values:
        if not isinstance(value, str) or not value:
            raise StateValidationError('deduplication keys must be non-empty strings')
        try:
            # El hash requiere UTF-8 válido; se valida antes de leer o modificar el estado persistido.
            value.encode('utf-8')
        except UnicodeEncodeError as error:
            raise StateValidationError('deduplication keys must be valid UTF-8 strings') from error
    return values


def _hash_key(value: str) -> str:
    # SHA-256 ofrece identidad estable sin persistir el message ID en texto claro.
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _parse_entries(value: Mapping[str, Any]) -> dict[str, float]:
    if set(value) != {'entries'} or not isinstance(value.get('entries'), Mapping):
        raise StateCorruptionError('expiring key set has an invalid payload')
    entries: dict[str, float] = {}
    for digest, expires_at in value['entries'].items():
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in '0123456789abcdef' for character in digest)
        ):
            raise StateCorruptionError('expiring key set contains an invalid key hash')
        if (
            not isinstance(expires_at, int | float)
            or isinstance(expires_at, bool)
            or not math.isfinite(expires_at)
        ):
            raise StateCorruptionError('expiring key set contains an invalid expiry')
        entries[digest] = float(expires_at)
    return entries


def _purge_entries(entries: dict[str, float], now_epoch: float) -> int:
    expired = [digest for digest, expires_at in entries.items() if expires_at <= now_epoch]
    for digest in expired:
        del entries[digest]
    return len(expired)


def _enforce_max_entries(entries: dict[str, float], max_entries: int) -> int:
    overflow = max(0, len(entries) - max_entries)
    if not overflow:
        return 0
    # Se eliminan primero los vencimientos más cercanos; el hash desempata determinísticamente.
    for digest, _ in sorted(entries.items(), key=lambda item: (item[1], item[0]))[:overflow]:
        del entries[digest]
    return overflow


def _utc_now() -> datetime:
    return datetime.now(UTC)
