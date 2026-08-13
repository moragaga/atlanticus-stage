# Espejo pedagógico: reproduce el código productivo y explica sus fronteras sin cambiar su comportamiento.
"""Store JSON pequeño con reemplazo atómico en el mismo filesystem."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from atlanticus.observability import (
    ErrorInfo,
    EventAudience,
    EventSeverity,
    ObservabilityLogger,
    get_observability_logger,
)
from atlanticus.state.errors import (
    StateCorruptionError,
    StateError,
    StateReadError,
    StateTooLargeError,
    StateValidationError,
    StateWriteError,
)
from atlanticus.state.models import StateDocument, StateKey, validate_application
from atlanticus.state.serialization import decode_json_object, encode_canonical_json

DEFAULT_MAX_DOCUMENT_BYTES = 1024 * 1024


# La clase siguiente mantiene una responsabilidad explícita y valida su propio contrato.


class AtomicStateStore:
    """Mantiene un único documento actual por clave lógica."""

    # La función siguiente concentra una operación verificable sin depender de estado implícito.

    def __init__(
        self,
        *,
        volume_path: str | Path,
        application: str,
        max_document_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES,
        clock: Callable[[], datetime] | None = None,
        logger: ObservabilityLogger | None = None,
    ) -> None:
        if not isinstance(volume_path, str | Path):
            raise StateValidationError('volume_path must be a filesystem path')
        if isinstance(volume_path, str) and volume_path != volume_path.strip():
            raise StateValidationError('volume_path must not contain surrounding whitespace')
        if not str(volume_path):
            raise StateValidationError('volume_path must not be empty')
        resolved_volume_path = Path(volume_path)
        if not resolved_volume_path.is_absolute():
            raise StateValidationError('volume_path must be an absolute path')
        if (
            not isinstance(max_document_bytes, int)
            or isinstance(max_document_bytes, bool)
            or max_document_bytes <= 0
        ):
            raise StateValidationError('max_document_bytes must be greater than zero')
        # Las dependencias inyectables fallan al construir el store, antes de tocar archivos.
        resolved_clock = _utc_now if clock is None else clock
        if not callable(resolved_clock):
            raise StateValidationError('clock must be callable')
        resolved_logger = get_observability_logger('atlanticus.state') if logger is None else logger
        if not callable(getattr(resolved_logger, 'log', None)):
            raise StateValidationError('logger must provide a callable log method')
        self._application = validate_application(application)
        self._application_root = resolved_volume_path / self._application
        self._state_root = self._application_root / '.runtime' / 'state'
        self._max_document_bytes = max_document_bytes
        self._clock = resolved_clock
        self._logger = resolved_logger
        self._write_lock = threading.RLock()

    @property
    # La función siguiente concentra una operación verificable sin depender de estado implícito.
    def application_root(self) -> Path:
        """Directorio dueño de los scopes de la aplicación."""

        return self._application_root

    @property
    def state_root(self) -> Path:
        """Directorio reservado para documentos de estado de la aplicación."""

        return self._state_root

    # La función siguiente concentra una operación verificable sin depender de estado implícito.

    def path_for(self, key: StateKey) -> Path:
        """Resuelve una clave ya validada sin acceder al filesystem."""

        return self._state_root / _require_state_key(key).relative_path

    # La función siguiente concentra una operación verificable sin depender de estado implícito.

    def read(self, key: StateKey) -> StateDocument | None:
        """Lee el último valor o retorna ``None`` si nunca fue publicado."""

        started = monotonic()
        resolved_key = _require_state_key(key)
        path = self.path_for(resolved_key)
        try:
            try:
                # El límite se aplica durante la lectura y evita cargar documentos arbitrariamente grandes.
                with path.open('rb') as file_handle:
                    content = file_handle.read(self._max_document_bytes + 1)
            except FileNotFoundError:
                self._emit_success(
                    'state.read.missing',
                    'State document does not exist yet.',
                    resolved_key,
                    started,
                    metrics={'byte_count': 0},
                )
                return None
            except OSError as error:
                raise StateReadError(
                    f'could not read state document {resolved_key.identifier}'
                ) from error
            if len(content) > self._max_document_bytes:
                raise StateTooLargeError(
                    f'state document {resolved_key.identifier} exceeds '
                    f'{self._max_document_bytes} bytes'
                )
            try:
                payload = decode_json_object(content)
                document = StateDocument.from_payload(resolved_key, payload)
            except StateCorruptionError:
                raise
            except (RecursionError, TypeError, ValueError) as error:
                raise StateCorruptionError(
                    f'state document {resolved_key.identifier} is invalid'
                ) from error
        except StateError as error:
            self._emit_failure(
                'state.read.failed', 'State document read failed.', resolved_key, started, error
            )
            raise
        self._emit_success(
            'state.read.succeeded',
            'State document read succeeded.',
            resolved_key,
            started,
            metrics={'byte_count': len(content)},
        )
        return document

    # La función siguiente concentra una operación verificable sin depender de estado implícito.

    def replace(self, key: StateKey, value: Mapping[str, Any]) -> StateDocument:
        """Confirma un documento completo mediante ``fsync`` y ``os.replace``."""

        resolved_key = _require_state_key(key)
        started = monotonic()
        try:
            # El mismo lock cubre reloj, serialización y commit: un thread tardío no puede publicar metadata más antigua.
            with self._write_lock:
                if not isinstance(value, Mapping):
                    raise StateValidationError('state value must be a mapping')
                document = StateDocument(
                    key=resolved_key,
                    updated_at_utc=self._resolve_now(),
                    value=dict(value),
                )
                content = encode_canonical_json(document.to_payload()) + b'\n'
                if len(content) > self._max_document_bytes:
                    raise StateTooLargeError(
                        f'state document {resolved_key.identifier} exceeds '
                        f'{self._max_document_bytes} bytes'
                    )
                orphan_temporary_count = self._replace_bytes(self.path_for(resolved_key), content)
        except StateError as error:
            severity = (
                EventSeverity.WARNING
                if isinstance(error, StateTooLargeError | StateValidationError)
                else EventSeverity.ERROR
            )
            self._emit_failure(
                'state.write.failed',
                'State document write failed.',
                resolved_key,
                started,
                error,
                severity=severity,
            )
            raise
        except (OSError, TypeError, ValueError) as error:
            wrapped = StateWriteError(f'could not write state document {resolved_key.identifier}')
            self._emit_failure(
                'state.write.failed',
                'State document write failed.',
                resolved_key,
                started,
                wrapped,
            )
            raise wrapped from error
        self._emit_success(
            'state.write.succeeded',
            'State document write succeeded.',
            resolved_key,
            started,
            metrics={
                'byte_count': len(content),
                'orphan_temporary_count': orphan_temporary_count,
            },
        )
        if orphan_temporary_count:
            self._emit_orphan_recovery(resolved_key, orphan_temporary_count)
        return document

    # La función siguiente concentra una operación verificable sin depender de estado implícito.

    def _replace_bytes(self, path: Path, content: bytes) -> int:
        temporary_path = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
        orphan_temporary_count = 0
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            orphan_temporary_count = _remove_orphan_temporaries(path)
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o640,
            )
            with os.fdopen(descriptor, 'wb') as file_handle:
                file_handle.write(content)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(temporary_path, path)
            # El fsync del directorio hace durable también el rename, alineado con datasets-parquet.
            _fsync_directory(path.parent)
        except OSError as error:
            raise StateWriteError(f'could not replace state document {path.name}') from error
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        return orphan_temporary_count

    # La función siguiente concentra una operación verificable sin depender de estado implícito.

    def _resolve_now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise StateValidationError('state clock must return a datetime')
        if value.tzinfo is None:
            raise StateValidationError('state clock must return a timezone-aware datetime')
        return value.astimezone(UTC)

    # La función siguiente concentra una operación verificable sin depender de estado implícito.

    def _emit_success(
        self,
        event_name: str,
        message: str,
        key: StateKey,
        started: float,
        *,
        metrics: Mapping[str, int | float] | None = None,
    ) -> None:
        self._safe_log(
            EventSeverity.INFO,
            message,
            event_name=event_name,
            audience=EventAudience.LOCAL,
            metrics={'duration_ms': _elapsed_ms(started), **dict(metrics or {})},
            attributes={'state_key': key.identifier},
        )

    # La función siguiente concentra una operación verificable sin depender de estado implícito.

    def _emit_failure(
        self,
        event_name: str,
        message: str,
        key: StateKey,
        started: float,
        error: BaseException,
        *,
        severity: EventSeverity = EventSeverity.ERROR,
    ) -> None:
        self._safe_log(
            severity,
            message,
            event_name=event_name,
            audience=EventAudience.OPERATIONS,
            metrics={'duration_ms': _elapsed_ms(started)},
            attributes={'state_key': key.identifier},
            error=ErrorInfo.from_exception(error),
        )

    # La función siguiente concentra una operación verificable sin depender de estado implícito.

    def _emit_orphan_recovery(self, key: StateKey, removed_count: int) -> None:
        self._safe_log(
            EventSeverity.WARNING,
            'Orphan state temporary files were removed before the next commit.',
            event_name='state.temporary.recovered',
            audience=EventAudience.OPERATIONS,
            metrics={'removed_count': removed_count},
            attributes={'state_key': key.identifier},
        )

    # La observabilidad es secundaria: nunca redefine el resultado de la persistencia.
    def _safe_log(self, *args: Any, **kwargs: Any) -> None:
        try:
            self._logger.log(*args, **kwargs)
        except Exception:
            pass


# La función siguiente concentra una operación verificable sin depender de estado implícito.


def _remove_orphan_temporaries(path: Path) -> int:
    removed_count = 0
    for candidate in path.parent.glob(f'.{path.name}.*.tmp'):
        if not _is_owned_temporary(path, candidate):
            continue
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue
        removed_count += 1
    return removed_count


# La función siguiente concentra una operación verificable sin depender de estado implícito.


def _is_owned_temporary(path: Path, candidate: Path) -> bool:
    prefix = f'.{path.name}.'
    suffix = '.tmp'
    candidate_name = candidate.name
    if not candidate_name.startswith(prefix) or not candidate_name.endswith(suffix):
        return False
    token = candidate_name[len(prefix) : -len(suffix)]
    return len(token) == 32 and all(character in '0123456789abcdef' for character in token)


# La función siguiente concentra una operación verificable sin depender de estado implícito.


# Windows no soporta la misma semántica de fsync sobre directorios; en POSIX se exige la garantía fuerte.
def _fsync_directory(path: Path) -> None:
    if os.name == 'nt':
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _utc_now() -> datetime:
    return datetime.now(UTC)


# La función siguiente concentra una operación verificable sin depender de estado implícito.


def _elapsed_ms(started: float) -> float:
    return round(max(0.0, (monotonic() - started) * 1000), 3)


def _require_state_key(key: StateKey) -> StateKey:
    if not isinstance(key, StateKey):
        raise StateValidationError('key must be a StateKey')
    return key
