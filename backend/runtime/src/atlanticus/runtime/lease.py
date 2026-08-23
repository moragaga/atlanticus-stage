"""Lease de archivo renovable para evitar solapamientos accidentales del mismo job."""

from __future__ import annotations

import json
import math
import os
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any
from uuid import uuid4

from atlanticus.runtime._authority import JobAuthorityStore
from atlanticus.runtime.errors import (
    AtlanticusRuntimeError,
    ConcurrentExecutionError,
    LeaseOwnershipLostError,
    LeaseRenewalError,
)
from atlanticus.runtime.storage import resolve_runtime_root, validate_path_segment


@dataclass(frozen=True, slots=True)
class RecoveredLease:
    """Identidad mínima del proceso que perdió su lease por expiración."""

    run_id: str | None
    instance_id: str | None
    process_id: int | None
    acquired_at_utc: str | None
    expires_at_utc: str | None

    def __post_init__(self) -> None:
        for name in ('run_id', 'instance_id', 'acquired_at_utc', 'expires_at_utc'):
            value = getattr(self, name)
            if value is not None:
                _require_non_empty_string(value, name)
        if self.process_id is not None:
            if isinstance(self.process_id, bool) or not isinstance(self.process_id, int):
                raise TypeError('process_id must be an int')
            if self.process_id <= 0:
                raise ValueError('process_id must be greater than zero')


@dataclass(frozen=True, slots=True)
class LeaseAcquisition:
    """Resultado de adquirir o resolver la coordinación de un job."""

    waited_seconds: float
    recovered: RecoveredLease | None = None
    generation: int | None = None
    skipped_reason: str | None = None

    def __post_init__(self) -> None:
        _validate_non_negative_number(self.waited_seconds, 'waited_seconds')
        if self.recovered is not None and not isinstance(self.recovered, RecoveredLease):
            raise TypeError('recovered must be a RecoveredLease')
        if self.generation is not None:
            if isinstance(self.generation, bool) or not isinstance(self.generation, int):
                raise TypeError('generation must be an int')
            if self.generation < 0:
                raise ValueError('generation must be greater than or equal to zero')
        if self.skipped_reason is not None:
            if not isinstance(self.skipped_reason, str):
                raise TypeError('skipped_reason must be a string')
            if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', self.skipped_reason):
                raise ValueError('skipped_reason must use lower snake_case')

    @property
    def acquired(self) -> bool:
        return self.skipped_reason is None


class ExecutionLease:
    """Coordina un solo escritor y renueva su expiración mientras el proceso sigue vivo."""

    def __init__(
        self,
        *,
        volume_path: str | Path,
        application: str,
        service_name: str,
        module_name: str,
        run_id: str,
        lease_timeout_seconds: float,
        wait_seconds: float,
        poll_seconds: float = 1.0,
        renewal_seconds: float | None = None,
        job_key: str | None = None,
        instance_id: str | None = None,
        process_id: int | None = None,
        scheduled_at_utc: datetime | None = None,
        authority_deadline_utc: datetime | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        normalized_job_key = service_name if job_key is None else job_key
        validate_path_segment(application, name='application')
        validate_path_segment(service_name, name='service_name')
        job_key_segment = validate_path_segment(normalized_job_key, name='job_key')
        _require_non_empty_string(module_name, 'module_name')
        _require_non_empty_string(run_id, 'run_id')
        _validate_positive_number(lease_timeout_seconds, 'lease_timeout_seconds')
        _validate_non_negative_number(wait_seconds, 'wait_seconds')
        _validate_positive_number(poll_seconds, 'poll_seconds')
        if scheduled_at_utc is not None:
            _require_utc_datetime(scheduled_at_utc, 'scheduled_at_utc')
        if authority_deadline_utc is not None:
            _require_utc_datetime(authority_deadline_utc, 'authority_deadline_utc')
        if wall_clock is not None and not callable(wall_clock):
            raise TypeError('wall_clock must be callable')
        self._directory = resolve_runtime_root(volume_path, application=application) / 'leases'
        self._path = self._directory / f'{job_key_segment}.json'
        self._recovery_guard = self._directory / f'.{job_key_segment}.recovery'
        self._authority_store = JobAuthorityStore(
            volume_path=volume_path,
            application=application,
            job_key=normalized_job_key,
        )
        self._application = application
        self._service_name = service_name
        self._job_key = normalized_job_key
        self._module_name = module_name
        self._run_id = run_id
        self._lease_timeout_seconds = lease_timeout_seconds
        self._wait_seconds = wait_seconds
        self._poll_seconds = poll_seconds
        self._renewal_seconds = (
            min(30.0, lease_timeout_seconds / 3) if renewal_seconds is None else renewal_seconds
        )
        _validate_positive_number(self._renewal_seconds, 'renewal_seconds')
        if self._renewal_seconds >= lease_timeout_seconds:
            raise ValueError(
                'renewal_seconds must be greater than zero and lower than lease timeout'
            )
        resolved_instance_id = socket.gethostname() if instance_id is None else instance_id
        _require_non_empty_string(resolved_instance_id, 'instance_id')
        resolved_process_id = os.getpid() if process_id is None else process_id
        if isinstance(resolved_process_id, bool) or not isinstance(resolved_process_id, int):
            raise TypeError('process_id must be an int')
        if resolved_process_id <= 0:
            raise ValueError('process_id must be greater than zero')
        self._instance_id = resolved_instance_id
        self._process_id = resolved_process_id
        self._scheduled_at_utc = scheduled_at_utc
        self._authority_deadline_utc = authority_deadline_utc
        self._wall_clock = _utc_now if wall_clock is None else wall_clock
        self._owner_token = str(uuid4())
        self._generation: int | None = None
        self._acquired = False
        self._acquisition: LeaseAcquisition | None = None
        self._renewal_stop = Event()
        self._renewal_thread: Thread | None = None
        self._renewal_callback: Callable[[str], None] | None = None
        self._failure: AtlanticusRuntimeError | None = None
        self._state_lock = Lock()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def authority_path(self) -> Path:
        return self._authority_store.path

    @property
    def acquired(self) -> bool:
        return self._acquired

    @property
    def acquisition(self) -> LeaseAcquisition | None:
        return self._acquisition

    @property
    def generation(self) -> int | None:
        return self._generation

    @property
    def failure(self) -> AtlanticusRuntimeError | None:
        with self._state_lock:
            return self._failure

    def acquire(self) -> LeaseAcquisition:
        """Espera en intervalos cortos y recupera una lease expirada."""

        self._directory.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        recovered: RecoveredLease | None = None
        while True:
            guard_descriptor = self._try_acquire_recovery_guard()
            if guard_descriptor is not None:
                try:
                    existing = self._read_payload()
                    if existing is None or self._is_expired(existing):
                        if existing is not None:
                            recovered_now = self._recover_expired_under_guard(existing)
                            if recovered is None:
                                recovered = recovered_now
                        acquisition = self._acquire_under_guard(
                            started=started,
                            recovered=recovered,
                        )
                        if acquisition is not None:
                            return acquisition
                finally:
                    os.close(guard_descriptor)
                    self._recovery_guard.unlink(missing_ok=True)

            elapsed = time.monotonic() - started
            remaining = self._wait_seconds - elapsed
            if remaining <= 0:
                raise ConcurrentExecutionError(
                    f'job {self._job_key!r} is already running after waiting {elapsed:.3f} seconds'
                )
            time.sleep(min(self._poll_seconds, remaining))

    def start_renewal(self, *, on_lost: Callable[[str], None] | None = None) -> None:
        """Inicia un heartbeat independiente del trabajo de negocio."""

        if on_lost is not None and not callable(on_lost):
            raise TypeError('on_lost must be callable')
        if not self._acquired or self._renewal_thread is not None:
            return
        self._renewal_stop.clear()
        self._renewal_callback = on_lost
        self._renewal_thread = Thread(
            target=self._renewal_loop,
            name=f'lease-heartbeat-{self._job_key}',
            daemon=True,
        )
        self._renewal_thread.start()

    def stop_renewal(self) -> None:
        """Detiene el heartbeat sin modificar la propiedad del lease."""

        thread = self._renewal_thread
        if thread is None:
            return
        self._renewal_stop.set()
        thread.join(timeout=max(1.0, self._poll_seconds * 2))
        if thread.is_alive():
            self._mark_failure(
                LeaseRenewalError('Lease heartbeat did not stop'),
                'lease_renewal_failed',
            )
            return
        self._renewal_thread = None

    def renew(self) -> bool:
        """Extiende la expiración sólo cuando el lease sigue perteneciendo al proceso."""

        if not self._acquired or self._generation is None:
            return False
        guard_descriptor = self._try_acquire_recovery_guard()
        if guard_descriptor is None:
            raise LeaseRenewalError('Lease renewal guard is unavailable')
        try:
            existing = self._read_payload()
            if not self._owns_payload(existing):
                self._acquired = False
                return False
            now = self._now()
            expiration = self._expiration_from(now)
            if expiration is None:
                self._acquired = False
                return False
            payload = dict(existing)
            payload['expires_at_utc'] = expiration.isoformat()
            self._replace_payload(payload)
            return True
        finally:
            os.close(guard_descriptor)
            self._recovery_guard.unlink(missing_ok=True)

    def release(self, *, completed: bool = False) -> bool:
        """Elimina la lease sólo cuando todavía pertenece a este proceso."""

        if not isinstance(completed, bool):
            raise TypeError('completed must be a bool')
        self.stop_renewal()
        if self._renewal_thread is not None and self._renewal_thread.is_alive():
            return False
        if not self._acquired or self._generation is None:
            return False
        guard_descriptor = self._try_acquire_recovery_guard()
        if guard_descriptor is None:
            return False
        try:
            existing = self._read_payload()
            if not self._owns_payload(existing):
                self._acquired = False
                return False
            if completed and self._scheduled_at_utc is not None:
                state = self._authority_store.read()
                self._authority_store.mark_completed(
                    state,
                    generation=self._generation,
                    scheduled_at_utc=self._scheduled_at_utc,
                )
            try:
                self._path.unlink()
            except FileNotFoundError:
                released = False
            else:
                _fsync_directory(self._directory)
                released = True
            self._acquired = False
            return released
        finally:
            os.close(guard_descriptor)
            self._recovery_guard.unlink(missing_ok=True)

    def __enter__(self) -> ExecutionLease:
        acquisition = self.acquire()
        if not acquisition.acquired:
            raise ConcurrentExecutionError(
                f'job {self._job_key!r} did not acquire a lease: {acquisition.skipped_reason}'
            )
        self.start_renewal()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback_value: Any) -> None:
        if exc_type is None:
            self.release(completed=True)
            return
        try:
            self.release(completed=False)
        except Exception:
            return

    def _acquire_under_guard(
        self,
        *,
        started: float,
        recovered: RecoveredLease | None,
    ) -> LeaseAcquisition | None:
        now = self._now()
        if self._authority_deadline_utc is not None and now >= self._authority_deadline_utc:
            acquisition = LeaseAcquisition(
                waited_seconds=round(time.monotonic() - started, 6),
                recovered=recovered,
                skipped_reason='authority_window_elapsed',
            )
            self._acquisition = acquisition
            return acquisition

        state = self._authority_store.read()
        if (
            self._scheduled_at_utc is not None
            and state.last_completed_scheduled_at_utc is not None
            and state.last_completed_scheduled_at_utc >= self._scheduled_at_utc
        ):
            acquisition = LeaseAcquisition(
                waited_seconds=round(time.monotonic() - started, 6),
                recovered=recovered,
                generation=state.generation,
                skipped_reason='scheduled_slot_completed',
            )
            self._acquisition = acquisition
            return acquisition

        advanced = self._authority_store.advance_generation(state)
        expiration = self._expiration_from(now)
        if expiration is None:
            acquisition = LeaseAcquisition(
                waited_seconds=round(time.monotonic() - started, 6),
                recovered=recovered,
                generation=advanced.generation,
                skipped_reason='authority_window_elapsed',
            )
            self._acquisition = acquisition
            return acquisition
        payload = self._new_payload(
            acquired_at=now,
            expires_at=expiration,
            generation=advanced.generation,
        )
        if not self._try_create_under_guard(payload):
            return None
        acquisition = LeaseAcquisition(
            waited_seconds=round(time.monotonic() - started, 6),
            recovered=recovered,
            generation=advanced.generation,
        )
        self._generation = advanced.generation
        self._acquired = True
        self._acquisition = acquisition
        with self._state_lock:
            self._failure = None
        return acquisition

    def _renewal_loop(self) -> None:
        while not self._renewal_stop.wait(self._renewal_seconds):
            try:
                renewed = self.renew()
            except Exception:
                self._mark_failure(
                    LeaseRenewalError('Lease renewal failed'),
                    'lease_renewal_failed',
                )
                return
            if not renewed:
                self._mark_failure(
                    LeaseOwnershipLostError('Lease ownership was lost'),
                    'lease_ownership_lost',
                )
                return

    def raise_if_unhealthy(self) -> None:
        failure = self.failure
        if failure is not None:
            raise failure

    def _mark_failure(self, error: AtlanticusRuntimeError, reason: str) -> None:
        callback: Callable[[str], None] | None
        with self._state_lock:
            if self._failure is not None:
                return
            self._failure = error
            callback = self._renewal_callback
        if callback is not None:
            try:
                callback(reason)
            except Exception:
                pass

    def _new_payload(
        self,
        *,
        acquired_at: datetime,
        expires_at: datetime,
        generation: int,
    ) -> dict[str, Any]:
        return {
            'schema_version': 2,
            'application': self._application,
            'service': self._service_name,
            'job_key': self._job_key,
            'module': self._module_name,
            'run_id': self._run_id,
            'instance_id': self._instance_id,
            'process_id': self._process_id,
            'owner_token': self._owner_token,
            'generation': generation,
            'scheduled_at_utc': (
                None if self._scheduled_at_utc is None else self._scheduled_at_utc.isoformat()
            ),
            'authority_deadline_utc': (
                None
                if self._authority_deadline_utc is None
                else self._authority_deadline_utc.isoformat()
            ),
            'acquired_at_utc': acquired_at.isoformat(),
            'expires_at_utc': expires_at.isoformat(),
        }

    def _try_create_under_guard(self, payload: dict[str, Any]) -> bool:
        descriptor: int | None = None
        try:
            try:
                descriptor = os.open(
                    self._path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o640,
                )
            except FileExistsError:
                return False
            try:
                _write_descriptor(descriptor, _encode_payload(payload))
            except BaseException:
                os.close(descriptor)
                descriptor = None
                self._path.unlink(missing_ok=True)
                _fsync_directory(self._directory)
                raise
            os.close(descriptor)
            descriptor = None
            _fsync_directory(self._directory)
            return True
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _replace_payload(self, payload: dict[str, Any]) -> None:
        temporary_path = self._path.with_name(f'.{self._path.name}.{uuid4().hex}.tmp')
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o640,
            )
            _write_descriptor(descriptor, _encode_payload(payload))
            os.close(descriptor)
            descriptor = None
            os.replace(temporary_path, self._path)
            _fsync_directory(self._directory)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)

    def _recover_expired_under_guard(self, existing: dict[str, Any]) -> RecoveredLease:
        recovered = _recovered_lease(existing)
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        else:
            _fsync_directory(self._directory)
        return recovered

    def _try_acquire_recovery_guard(self) -> int | None:
        try:
            return os.open(
                self._recovery_guard,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o640,
            )
        except FileExistsError:
            try:
                age_seconds = time.time() - self._recovery_guard.stat().st_mtime
            except FileNotFoundError:
                return None
            if age_seconds <= max(5.0, self._poll_seconds * 2):
                return None
            self._recovery_guard.unlink(missing_ok=True)
            try:
                return os.open(
                    self._recovery_guard,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o640,
                )
            except FileExistsError:
                return None

    def _read_payload(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self._path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except UnicodeError, json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _is_expired(self, payload: dict[str, Any]) -> bool:
        raw_expiration = payload.get('expires_at_utc')
        if not isinstance(raw_expiration, str):
            return True
        try:
            expiration = datetime.fromisoformat(raw_expiration)
        except ValueError:
            return True
        if expiration.tzinfo is None:
            return True
        return expiration <= self._now()

    def _owns_payload(self, payload: dict[str, Any] | None) -> bool:
        if payload is None or self._generation is None:
            return False
        generation = payload.get('generation')
        return payload.get('owner_token') == self._owner_token and generation == self._generation

    def _expiration_from(self, now: datetime) -> datetime | None:
        expiration = now + timedelta(seconds=self._lease_timeout_seconds)
        if self._authority_deadline_utc is not None:
            if now >= self._authority_deadline_utc:
                return None
            expiration = min(expiration, self._authority_deadline_utc)
        return expiration

    def _now(self) -> datetime:
        return _normalize_utc_datetime(self._wall_clock(), 'wall_clock result')


def _encode_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8') + b'\n'


def _write_descriptor(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError('could not write runtime lease')
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


def _recovered_lease(payload: dict[str, Any] | None) -> RecoveredLease:
    values = payload or {}
    process_id = values.get('process_id')
    return RecoveredLease(
        run_id=_optional_string(values.get('run_id')),
        instance_id=_optional_string(values.get('instance_id')),
        process_id=(
            process_id
            if isinstance(process_id, int) and not isinstance(process_id, bool) and process_id > 0
            else None
        ),
        acquired_at_utc=_optional_string(values.get('acquired_at_utc')),
        expires_at_utc=_optional_string(values.get('expires_at_utc')),
    )


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_utc_datetime(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f'{name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f'{name} must be timezone-aware')
    return value.astimezone(UTC)


def _require_utc_datetime(value: datetime, name: str) -> None:
    normalized = _normalize_utc_datetime(value, name)
    if normalized != value:
        raise ValueError(f'{name} must use UTC timezone')


def _require_non_empty_string(value: str, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f'{name} must be a string')
    if not value.strip():
        raise ValueError(f'{name} must not be empty')


def _validate_positive_number(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f'{name} must be an int or float')
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be a finite value greater than zero')


def _validate_non_negative_number(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f'{name} must be an int or float')
    if not math.isfinite(value) or value < 0:
        raise ValueError(f'{name} must be a finite value greater than or equal to zero')
