# Espejo pedagógico: concentra únicamente las primitivas físicas de escritura y commit atómico.
"""Primitivas físicas de escritura Parquet y commits atómicos."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from atlanticus.datasets.parquet._filesystem import _file_signature, _fsync_directory
from atlanticus.datasets.parquet.errors import (
    ParquetCorruptionError,
    ParquetSchemaError,
    ParquetValidationError,
    ParquetWriteError,
)
from atlanticus.datasets.parquet.manifest import _encode_manifest, _Manifest, _ManifestPart
from atlanticus.datasets.parquet.models import ParquetWriteOptions, _Artifact


# Escribe el artefacto único en un temporal, lo valida y sólo después reemplaza data.parquet.
def _replace_single_artifact(
    *,
    target_path: Path,
    target_identifier: str,
    table: pa.Table,
    write_options: ParquetWriteOptions,
) -> _Artifact:
    final_path = target_path / 'data.parquet'
    temporary_path: Path | None = None
    try:
        target_path.mkdir(parents=True, exist_ok=True)
        temporary_path = target_path / f'.data.parquet.{uuid4().hex}.tmp'
        _write_and_validate_table(
            path=temporary_path,
            table=table,
            write_options=write_options,
        )
        # El tamaño y hash se calculan sobre el temporal ya persistido y validado.
        size_bytes = temporary_path.stat().st_size
        content_signature = _file_signature(temporary_path)
        # os.replace y fsync del directorio constituyen el punto físico de confirmación.
        os.replace(temporary_path, final_path)
        _fsync_directory(target_path)
    except ParquetValidationError:
        raise
    except (OSError, pa.ArrowException) as error:
        raise ParquetWriteError(
            f'could not replace parquet publication {target_identifier}'
        ) from error
    finally:
        # Un fallo nunca debe dejar el temporal bajo control del writer.
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
    return _Artifact(
        path=final_path,
        schema=table.schema,
        item_count=table.num_rows,
        size_bytes=size_bytes,
        content_signature=content_signature,
    )


# Materializa una parte content-addressed; si el hash ya existe, reutiliza el archivo confirmado.
def _write_content_part(
    *,
    target_path: Path,
    part_dimension: str,
    part_value: str,
    table: pa.Table,
    write_options: ParquetWriteOptions,
) -> tuple[_ManifestPart, bool]:
    name_template = f'{part_dimension}={part_value}--{"0" * 64}.parquet'
    if len(os.fsencode(name_template)) > 255:
        raise ParquetValidationError('part identity produces a filename longer than 255 bytes')
    target_path.mkdir(parents=True, exist_ok=True)
    temporary_path = target_path / f'.{part_dimension}={part_value}.{uuid4().hex}.tmp'
    try:
        _write_and_validate_table(
            path=temporary_path,
            table=table,
            write_options=write_options,
        )
        size_bytes = temporary_path.stat().st_size
        content_signature = _file_signature(temporary_path)
        digest = content_signature.removeprefix('sha256:')
        file_name = f'{part_dimension}={part_value}--{digest}.parquet'
        final_path = target_path / file_name
        was_created = False
        # Un nombre content-addressed existente sólo es reutilizable si su contenido coincide.
        if final_path.exists():
            if _file_signature(final_path) != content_signature:
                raise ParquetCorruptionError(
                    f'content-addressed parquet part is inconsistent: {file_name}'
                )
            temporary_path.unlink()
        else:
            os.replace(temporary_path, final_path)
            _fsync_directory(target_path)
            was_created = True
        return (
            _ManifestPart(
                value=part_value,
                path=file_name,
                item_count=table.num_rows,
                size_bytes=size_bytes,
                content_signature=content_signature,
            ),
            was_created,
        )
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


# Centraliza las opciones Parquet y verifica que lo escrito conserve filas y schema exactos.
def _write_and_validate_table(
    *,
    path: Path,
    table: pa.Table,
    write_options: ParquetWriteOptions,
) -> None:
    pq.write_table(
        table,
        path,
        compression=write_options.compression,
        compression_level=write_options.compression_level,
        use_dictionary=write_options.use_dictionary,
        write_statistics=write_options.write_statistics,
        row_group_size=write_options.row_group_size,
    )
    # El fsync del archivo ocurre antes de inspeccionar metadata y antes de cualquier rename.
    with path.open('rb') as file_handle:
        os.fsync(file_handle.fileno())
    parquet_file = pq.ParquetFile(path)
    if parquet_file.metadata.num_rows != table.num_rows:
        raise ParquetWriteError('written parquet row count does not match the source table')
    if not parquet_file.schema_arrow.equals(table.schema, check_metadata=True):
        raise ParquetSchemaError('written parquet schema does not match the source table')


# current.json usa el mismo patrón temporal + fsync + replace que los artefactos Parquet.
def _replace_manifest(*, target_path: Path, manifest: _Manifest) -> None:
    target_path.mkdir(parents=True, exist_ok=True)
    final_path = target_path / 'current.json'
    temporary_path = target_path / f'.current.json.{uuid4().hex}.tmp'
    content = _encode_manifest(manifest)
    try:
        descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o666,
        )
        with os.fdopen(descriptor, 'wb') as file_handle:
            file_handle.write(content)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temporary_path, final_path)
        _fsync_directory(target_path)
    except OSError as error:
        raise ParquetWriteError('could not replace parquet current manifest') from error
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
