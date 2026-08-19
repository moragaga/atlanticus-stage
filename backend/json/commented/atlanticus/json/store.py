from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from atlanticus.json.errors import (
    JsonConflictError,
    JsonCorruptionError,
    JsonReadError,
    JsonValidationError,
    JsonWriteError,
)
from atlanticus.json.serialization import (
    JsonDocument,
    decode_json_document,
    encode_json_document,
    normalize_json_document,
)


# write_once informa si creó el documento o si un reintento encontró exactamente el mismo contenido.
class JsonWriteOnceStatus(StrEnum):
    CREATED = 'created'
    UNCHANGED = 'unchanged'


class JsonDocumentStore:
    def __init__(self) -> None:
        # El lock sólo coordina writers del mismo proceso. La exclusión entre procesos pertenece a la capa superior.
        self._write_lock = threading.RLock()

    def exists(self, path: str | Path) -> bool:
        resolved_path = _require_absolute_path(path)
        try:
            return resolved_path.exists()
        except OSError as error:
            raise JsonReadError(f'could not inspect JSON document {resolved_path}') from error

    def read(self, path: str | Path) -> JsonDocument | None:
        resolved_path = _require_absolute_path(path)
        try:
            try:
                content = resolved_path.read_bytes()
            except FileNotFoundError:
                return None
            except OSError as error:
                raise JsonReadError(f'could not read JSON document {resolved_path}') from error
            return decode_json_document(content)
        except JsonCorruptionError:
            # Un archivo corrupto no se trata como inexistente: el caller debe decidir cómo recuperarlo.
            raise

    def replace(self, path: str | Path, document: Mapping[str, Any]) -> None:
        resolved_path = _require_absolute_path(path)
        content = _document_bytes(document)
        with self._write_lock:
            self._replace_bytes(resolved_path, content)

    def write_once(
        self,
        path: str | Path,
        document: Mapping[str, Any],
    ) -> JsonWriteOnceStatus:
        resolved_path = _require_absolute_path(path)
        normalized = _normalize_document(document)
        content = encode_json_document(normalized) + b'\n'
        with self._write_lock:
            # Un documento ya confirmado sólo es idempotente si representa exactamente el mismo JSON normalizado.
            existing = self.read(resolved_path)
            if existing is not None:
                if existing == normalized:
                    return JsonWriteOnceStatus.UNCHANGED
                raise JsonConflictError(
                    f'JSON document already exists with different content: {resolved_path}'
                )
            self._replace_bytes(resolved_path, content)
            return JsonWriteOnceStatus.CREATED

    @staticmethod
    def _replace_bytes(path: Path, content: bytes) -> None:
        # El temporal vive junto al destino para que os.replace ocurra dentro del mismo filesystem.
        temporary_path = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o640,
            )
            with os.fdopen(descriptor, 'wb') as file_handle:
                file_handle.write(content)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            # Los readers observan el documento anterior completo o el nuevo completo, nunca el temporal.
            os.replace(temporary_path, path)
            _fsync_directory(path.parent)
        except OSError as error:
            raise JsonWriteError(f'could not write JSON document {path}') from error
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _require_absolute_path(path: str | Path) -> Path:
    # La resolución de VOLUMEN_PATH, APPLICATION o cualquier layout pertenece a la capability consumidora.
    if not isinstance(path, str | Path):
        raise JsonValidationError('path must be a filesystem path')
    if isinstance(path, str) and not path:
        raise JsonValidationError('path must not be empty')
    resolved_path = Path(path)
    if not resolved_path.is_absolute():
        raise JsonValidationError('path must be absolute')
    return resolved_path


def _normalize_document(document: Mapping[str, Any]) -> JsonDocument:
    return normalize_json_document(document)


def _document_bytes(document: Mapping[str, Any]) -> bytes:
    return encode_json_document(document) + b'\n'


def _fsync_directory(path: Path) -> None:
    # Confirmar el directorio completa la durabilidad del rename en filesystems que soportan fsync de directorio.
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
