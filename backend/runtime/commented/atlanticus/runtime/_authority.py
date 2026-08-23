# Este módulo conserva la autoridad durable mínima del job: generación monotónica y último slot completado.
# No contiene lógica de negocio ni conoce Azure; sólo persiste coordinación técnica reutilizable.

"""Estado durable de autoridad para generaciones y slots programados."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from atlanticus.runtime.errors import AtlanticusRuntimeError
from atlanticus.runtime.storage import resolve_runtime_root, validate_path_segment


@dataclass(frozen=True, slots=True)
class JobAuthorityState:
    generation: int = 0
    last_completed_scheduled_at_utc: datetime | None = None

    def __post_init__(self) -> None:
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise TypeError('generation must be an int')
        if self.generation < 0:
            raise ValueError('generation must be greater than or equal to zero')
        if self.last_completed_scheduled_at_utc is not None:
            _require_utc_datetime(
                self.last_completed_scheduled_at_utc,
                'last_completed_scheduled_at_utc',
            )


class JobAuthorityStore:
    def __init__(
        self,
        *,
        volume_path: str | Path,
        application: str,
        job_key: str,
    ) -> None:
        job_key_segment = validate_path_segment(job_key, name='job_key')
        self._directory = resolve_runtime_root(volume_path, application=application) / 'authority'
        self._path = self._directory / f'{job_key_segment}.json'

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> JobAuthorityState:
        try:
            raw = self._path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return JobAuthorityState()
        except UnicodeError as error:
            raise AtlanticusRuntimeError('Runtime authority state is invalid') from error
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise AtlanticusRuntimeError('Runtime authority state is invalid') from error
        if not isinstance(value, dict) or value.get('schema_version') != 1:
            raise AtlanticusRuntimeError('Runtime authority state is invalid')
        generation = value.get('generation')
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise AtlanticusRuntimeError('Runtime authority state is invalid')
        raw_completed = value.get('last_completed_scheduled_at_utc')
        completed = _optional_utc_datetime(raw_completed)
        if raw_completed is not None and completed is None:
            raise AtlanticusRuntimeError('Runtime authority state is invalid')
        return JobAuthorityState(
            generation=generation,
            last_completed_scheduled_at_utc=completed,
        )

    def advance_generation(self, state: JobAuthorityState) -> JobAuthorityState:
        if not isinstance(state, JobAuthorityState):
            raise TypeError('state must be a JobAuthorityState')
        updated = JobAuthorityState(
            generation=state.generation + 1,
            last_completed_scheduled_at_utc=state.last_completed_scheduled_at_utc,
        )
        self.write(updated)
        return updated

    def mark_completed(
        self,
        state: JobAuthorityState,
        *,
        generation: int,
        scheduled_at_utc: datetime,
    ) -> JobAuthorityState:
        if not isinstance(state, JobAuthorityState):
            raise TypeError('state must be a JobAuthorityState')
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise TypeError('generation must be an int')
        if generation <= 0:
            raise ValueError('generation must be greater than zero')
        _require_utc_datetime(scheduled_at_utc, 'scheduled_at_utc')
        if state.generation != generation:
            raise AtlanticusRuntimeError('Runtime authority generation changed unexpectedly')
        completed = state.last_completed_scheduled_at_utc
        if completed is not None and completed >= scheduled_at_utc:
            return state
        updated = JobAuthorityState(
            generation=state.generation,
            last_completed_scheduled_at_utc=scheduled_at_utc,
        )
        self.write(updated)
        return updated

    def write(self, state: JobAuthorityState) -> None:
        if not isinstance(state, JobAuthorityState):
            raise TypeError('state must be a JobAuthorityState')
        self._directory.mkdir(parents=True, exist_ok=True)
        payload = {
            'schema_version': 1,
            'generation': state.generation,
            'last_completed_scheduled_at_utc': (
                None
                if state.last_completed_scheduled_at_utc is None
                else state.last_completed_scheduled_at_utc.isoformat()
            ),
        }
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8') + b'\n'
        temporary_path = self._path.with_name(f'.{self._path.name}.{uuid4().hex}.tmp')
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o640,
            )
            _write_descriptor(descriptor, content)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary_path, self._path)
            _fsync_directory(self._directory)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)


def _optional_utc_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    normalized = parsed.astimezone(UTC)
    if normalized != parsed:
        return None
    return normalized


def _require_utc_datetime(value: datetime, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f'{name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f'{name} must be timezone-aware')
    if value.astimezone(UTC) != value:
        raise ValueError(f'{name} must use UTC timezone')


def _write_descriptor(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError('could not write runtime authority state')
        view = view[written:]
    os.fsync(descriptor)


def _fsync_directory(directory: Path) -> None:
    if os.name == 'nt':
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
