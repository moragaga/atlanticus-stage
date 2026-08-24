"""Exclusión física de corta duración para mutaciones autoritativas del runtime."""

from __future__ import annotations

import errno
import os
import time
from pathlib import Path

from atlanticus.runtime.storage import resolve_runtime_root, validate_path_segment

if os.name == 'nt':
    import msvcrt
else:
    import fcntl


# Implementación privada: el dominio sólo ve context.fenced_mutation().
class PhysicalAuthorityFence:
    def __init__(
        self,
        *,
        volume_path: str | Path,
        application: str,
        job_key: str,
    ) -> None:
        job_key_segment = validate_path_segment(job_key, name='job_key')
        self._directory = resolve_runtime_root(volume_path, application=application) / 'fences'
        self._path = self._directory / f'{job_key_segment}.lock'

    @property
    def path(self) -> Path:
        return self._path

    # Cada intento abre una descripción independiente; flock las hace competir incluso en el mismo proceso.
    def try_acquire(self) -> int | None:
        self._directory.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o640)
        try:
            if _try_lock_descriptor(descriptor):
                return descriptor
        except BaseException:
            os.close(descriptor)
            raise
        os.close(descriptor)
        return None

    def acquire(self, *, wait_seconds: float, poll_seconds: float) -> int | None:
        if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, int | float):
            raise TypeError('wait_seconds must be an int or float')
        if wait_seconds < 0:
            raise ValueError('wait_seconds must be greater than or equal to zero')
        if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, int | float):
            raise TypeError('poll_seconds must be an int or float')
        if poll_seconds <= 0:
            raise ValueError('poll_seconds must be greater than zero')
        deadline = time.monotonic() + float(wait_seconds)
        while True:
            descriptor = self.try_acquire()
            if descriptor is not None:
                return descriptor
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(float(poll_seconds), remaining))

    @staticmethod
    def release(descriptor: int) -> None:
        if isinstance(descriptor, bool) or not isinstance(descriptor, int):
            raise TypeError('descriptor must be an int')
        try:
            _unlock_descriptor(descriptor)
        finally:
            os.close(descriptor)


def _try_lock_descriptor(descriptor: int) -> bool:
    if os.name == 'nt':
        # Windows necesita una región existente para msvcrt.locking().
        _ensure_lock_byte(descriptor)
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True
    try:
        # En Linux/CIFS moderno este flock se propaga al servidor SMB como lock de archivo.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EAGAIN}:
            return False
        raise
    return True


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == 'nt':
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _ensure_lock_byte(descriptor: int) -> None:
    if os.fstat(descriptor).st_size > 0:
        return
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.write(descriptor, b'\0')
    os.fsync(descriptor)
