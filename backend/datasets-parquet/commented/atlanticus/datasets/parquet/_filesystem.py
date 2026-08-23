# Espejo pedagógico: concentra hashing, nombres físicos y fsync sin ampliar la API pública.
"""Helpers privados de filesystem para persistencia Parquet."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from atlanticus.datasets.parquet.errors import ParquetCorruptionError
from atlanticus.datasets.parquet.manifest import _ManifestPart

_PART_SIGNATURE_PATTERN = re.compile(r'^[0-9a-f]{64}$')
_TEMPORARY_PATTERN = re.compile(r'^\..+\.[0-9a-f]{32}\.tmp$')


# El hash se calcula en streaming para no cargar el artefacto completo en memoria.
def _file_signature(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as file_handle:
        while content := file_handle.read(1024 * 1024):
            digest.update(content)
    return f'sha256:{digest.hexdigest()}'


def _is_owned_part_filename(name: str, *, part_dimension: str) -> bool:
    prefix = f'{part_dimension}='
    suffix = '.parquet'
    if not name.startswith(prefix) or not name.endswith(suffix) or '--' not in name:
        return False
    value_and_digest = name[len(prefix) : -len(suffix)]
    value, digest = value_and_digest.rsplit('--', 1)
    return bool(value) and bool(_PART_SIGNATURE_PATTERN.fullmatch(digest))


# El nombre content-addressed debe concordar exactamente con la firma confirmada en el manifest.
def _validate_part_filename(*, part: _ManifestPart, part_dimension: str) -> None:
    digest = part.content_signature.removeprefix('sha256:')
    if not _PART_SIGNATURE_PATTERN.fullmatch(digest):
        raise ParquetCorruptionError('parquet part signature is invalid')
    expected = f'{part_dimension}={part.value}--{digest}.parquet'
    if part.path != expected:
        raise ParquetCorruptionError(
            f'parquet part filename does not match its identity: {part.path}'
        )


def _is_temporary_filename(name: str) -> bool:
    return bool(_TEMPORARY_PATTERN.fullmatch(name))


def _fsync_directory(path: Path) -> None:
    if os.name == 'nt':
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
