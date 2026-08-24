"""Stores JSON compactos con reemplazo atómico en el mismo filesystem."""

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
from atlanticus.state.serialization import (
    JsonObject,
    decode_json_object,
    encode_canonical_json,
    normalize_json_object,
)

DEFAULT_MAX_DOCUMENT_BYTES = 1024 * 1024


class AtomicJsonStore:
    """Persiste objetos JSON sin sobre de dominio bajo una raíz explícita."""

    def __init__(
        self,
        *,
        root_path: str | Path,
        max_document_bytes: int | None = DEFAULT_MAX_DOCUMENT_BYTES,
    ) -> None:
        self._root_path = _require_absolute_path(root_path, field_name='root_path')
        self._max_document_bytes = _validate_max_document_bytes(max_document_bytes)
        self._write_lock = threading.RLock()

    @property
    def root_path(self) -> Path:
        """Raíz física que delimita todas las rutas relativas del store."""

        return self._root_path

    def path_for(self, relative_path: str | Path) -> Path:
        """Resuelve una ruta JSON relativa segura sin acceder al filesystem."""

        return self._root_path / _require_relative_json_path(relative_path)

    def read(self, relative_path: str | Path) -> JsonObject | None:
        """Lee el último objeto confirmado o retorna ``None`` si no existe."""

        resolved_relative_path = _require_relative_json_path(relative_path)
        path = self._root_path / resolved_relative_path
        try:
            content = _read_bytes(path, self._max_document_bytes)
        except FileNotFoundError:
            return None
        except StateTooLargeError as error:
            raise StateTooLargeError(
                f'JSON document {resolved_relative_path.as_posix()} exceeds '
                f'{self._max_document_bytes} bytes'
            ) from error
        except OSError as error:
            raise StateReadError(
                f'could not read JSON document {resolved_relative_path.as_posix()}'
            ) from error
        return decode_json_object(content)

    def replace(self, relative_path: str | Path, value: Mapping[str, Any]) -> JsonObject:
        """Confirma un objeto JSON completo mediante ``fsync`` y ``os.replace``."""

        resolved_relative_path = _require_relative_json_path(relative_path)
        path = self._root_path / resolved_relative_path
        try:
            with self._write_lock:
                normalized = normalize_json_object(value)
                content = encode_canonical_json(normalized) + b'\n'
                try:
                    _enforce_document_size(content, self._max_document_bytes)
                except StateTooLargeError as error:
                    raise StateTooLargeError(
                        f'JSON document {resolved_relative_path.as_posix()} exceeds '
                        f'{self._max_document_bytes} bytes'
                    ) from error
                _replace_bytes(path, content)
        except StateError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise StateWriteError(
                f'could not write JSON document {resolved_relative_path.as_posix()}'
            ) from error
        return normalized


class AtomicStateStore:
    """Mantiene un único documento actual por clave lógica."""

    def __init__(
        self,
        *,
        volume_path: str | Path,
        application: str,
        max_document_bytes: int | None = DEFAULT_MAX_DOCUMENT_BYTES,
        clock: Callable[[], datetime] | None = None,
        logger: ObservabilityLogger | None = None,
    ) -> None:
        resolved_volume_path = _require_absolute_path(volume_path, field_name='volume_path')
        resolved_max_document_bytes = _validate_max_document_bytes(max_document_bytes)
        resolved_clock = _utc_now if clock is None else clock
        if not callable(resolved_clock):
            raise StateValidationError('clock must be callable')
        resolved_logger = get_observability_logger('atlanticus.state') if logger is None else logger
        if not callable(getattr(resolved_logger, 'log', None)):
            raise StateValidationError('logger must provide a callable log method')
        self._application = validate_application(application)
        self._application_root = resolved_volume_path / self._application
        self._state_root = self._application_root / '.runtime' / 'state'
        self._max_document_bytes = resolved_max_document_bytes
        self._clock = resolved_clock
        self._logger = resolved_logger
        self._write_lock = threading.RLock()

    @property
    def application_root(self) -> Path:
        """Directorio dueño de los scopes de la aplicación."""

        return self._application_root

    @property
    def state_root(self) -> Path:
        """Directorio reservado para documentos de estado de la aplicación."""

        return self._state_root

    def path_for(self, key: StateKey) -> Path:
        """Resuelve una clave ya validada sin acceder al filesystem."""

        return self._state_root / _require_state_key(key).relative_path

    def read(self, key: StateKey) -> StateDocument | None:
        """Lee el último valor o retorna ``None`` si nunca fue publicado."""

        started = monotonic()
        resolved_key = _require_state_key(key)
        path = self.path_for(resolved_key)
        try:
            try:
                content = _read_bytes(path, self._max_document_bytes)
            except FileNotFoundError:
                self._emit_success(
                    'state.read.missing',
                    'State document does not exist yet.',
                    resolved_key,
                    started,
                    metrics={'byte_count': 0},
                )
                return None
            except StateTooLargeError as error:
                raise StateTooLargeError(
                    f'state document {resolved_key.identifier} exceeds '
                    f'{self._max_document_bytes} bytes'
                ) from error
            except OSError as error:
                raise StateReadError(
                    f'could not read state document {resolved_key.identifier}'
                ) from error
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

    def replace(self, key: StateKey, value: Mapping[str, Any]) -> StateDocument:
        """Confirma un documento completo mediante ``fsync`` y ``os.replace``."""

        resolved_key = _require_state_key(key)
        started = monotonic()
        try:
            with self._write_lock:
                if not isinstance(value, Mapping):
                    raise StateValidationError('state value must be a mapping')
                document = StateDocument(
                    key=resolved_key,
                    updated_at_utc=self._resolve_now(),
                    value=dict(value),
                )
                content = encode_canonical_json(document.to_payload()) + b'\n'
                try:
                    _enforce_document_size(content, self._max_document_bytes)
                except StateTooLargeError as error:
                    raise StateTooLargeError(
                        f'state document {resolved_key.identifier} exceeds '
                        f'{self._max_document_bytes} bytes'
                    ) from error
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

    def _replace_bytes(self, path: Path, content: bytes) -> int:
        return _replace_bytes(path, content)

    def _resolve_now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise StateValidationError('state clock must return a datetime')
        if value.tzinfo is None:
            raise StateValidationError('state clock must return a timezone-aware datetime')
        return value.astimezone(UTC)

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

    def _emit_orphan_recovery(self, key: StateKey, removed_count: int) -> None:
        self._safe_log(
            EventSeverity.WARNING,
            'Orphan state temporary files were removed before the next commit.',
            event_name='state.temporary.recovered',
            audience=EventAudience.OPERATIONS,
            metrics={'removed_count': removed_count},
            attributes={'state_key': key.identifier},
        )

    def _safe_log(self, *args: Any, **kwargs: Any) -> None:
        try:
            self._logger.log(*args, **kwargs)
        except Exception:
            pass


def _read_bytes(path: Path, max_document_bytes: int | None) -> bytes:
    with path.open('rb') as file_handle:
        if max_document_bytes is None:
            return file_handle.read()
        content = file_handle.read(max_document_bytes + 1)
    _enforce_document_size(content, max_document_bytes)
    return content


def _replace_bytes(path: Path, content: bytes) -> int:
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
        _fsync_directory(path.parent)
    except OSError as error:
        raise StateWriteError(f'could not replace JSON document {path.name}') from error
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
    return orphan_temporary_count


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


def _is_owned_temporary(path: Path, candidate: Path) -> bool:
    prefix = f'.{path.name}.'
    suffix = '.tmp'
    candidate_name = candidate.name
    if not candidate_name.startswith(prefix) or not candidate_name.endswith(suffix):
        return False
    token = candidate_name[len(prefix) : -len(suffix)]
    return len(token) == 32 and all(character in '0123456789abcdef' for character in token)


def _fsync_directory(path: Path) -> None:
    if os.name == 'nt':
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_absolute_path(value: str | Path, *, field_name: str) -> Path:
    if not isinstance(value, str | Path):
        raise StateValidationError(f'{field_name} must be a filesystem path')
    if isinstance(value, str) and value != value.strip():
        raise StateValidationError(f'{field_name} must not contain surrounding whitespace')
    if not str(value):
        raise StateValidationError(f'{field_name} must not be empty')
    resolved = Path(value)
    if not resolved.is_absolute():
        raise StateValidationError(f'{field_name} must be an absolute path')
    return resolved


def _require_relative_json_path(value: str | Path) -> Path:
    if not isinstance(value, str | Path):
        raise StateValidationError('relative_path must be a filesystem path')
    if isinstance(value, str) and value != value.strip():
        raise StateValidationError('relative_path must not contain surrounding whitespace')
    if not str(value):
        raise StateValidationError('relative_path must not be empty')
    resolved = Path(value)
    if resolved == Path('.') or resolved.is_absolute() or resolved.drive:
        raise StateValidationError('relative_path must be a relative JSON path')
    if any(part in {'.', '..'} for part in resolved.parts):
        raise StateValidationError('relative_path must not contain relative path segments')
    if resolved.suffix != '.json':
        raise StateValidationError('relative_path must end with .json')
    return resolved


def _validate_max_document_bytes(value: int | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise StateValidationError('max_document_bytes must be greater than zero or None')
    return value


def _enforce_document_size(content: bytes, max_document_bytes: int | None) -> None:
    if max_document_bytes is not None and len(content) > max_document_bytes:
        raise StateTooLargeError('document exceeds max_document_bytes')


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _elapsed_ms(started: float) -> float:
    return round(max(0.0, (monotonic() - started) * 1000), 3)


def _require_state_key(key: StateKey) -> StateKey:
    if not isinstance(key, StateKey):
        raise StateValidationError('key must be a StateKey')
    return key
