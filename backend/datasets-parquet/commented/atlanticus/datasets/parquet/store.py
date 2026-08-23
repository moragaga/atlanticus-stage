# Espejo pedagógico: el store conserva la fachada pública y delega helpers internos sin cambiar contratos.
"""Store Parquet atómico basado exclusivamente en tablas PyArrow."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from atlanticus.datasets.layouts import FileSetLayout, SingleArtifactLayout
from atlanticus.datasets.models import DatasetDefinition, DatasetPartKey, DatasetTarget
from atlanticus.datasets.parquet._filesystem import (
    _file_signature,
    _fsync_directory,
    _is_owned_part_filename,
    _is_temporary_filename,
)
from atlanticus.datasets.parquet._publication import (
    _read_file_signature,
    _read_manifest,
    _resolve_publication,
    _validate_preserved_parts,
)
from atlanticus.datasets.parquet._scan import _scan_publications
from atlanticus.datasets.parquet._validation import (
    _align_table as _align_table_impl,
    _normalize_columns,
    _normalize_incoming_parts as _normalize_incoming_parts_impl,
    _normalize_remove_parts as _normalize_remove_parts_impl,
    _normalize_targets,
    _validate_key_values as _validate_key_values_impl,
    _validate_merge_columns as _validate_merge_columns_impl,
    _validate_schema as _validate_schema_impl,
    _validate_table as _validate_table_impl,
)
from atlanticus.datasets.parquet.errors import (
    ParquetCorruptionError,
    ParquetLayoutError,
    ParquetPublicationNotFoundError,
    ParquetSchemaError,
    ParquetValidationError,
    ParquetWriteError,
)
from atlanticus.datasets.parquet.manifest import (
    _encode_manifest,
    _Manifest,
    _ManifestPart,
    _publication_signature,
    _schema_signature,
)
from atlanticus.datasets.parquet.models import (
    ColumnFilter,
    ParquetCleanupResult,
    ParquetPart,
    ParquetReadResult,
    ParquetWriteOptions,
    _Artifact,
)
from atlanticus.datasets.results import (
    DatasetPublicationResult,
    PublicationQuality,
    PublicationStatus,
)


class ParquetDatasetStore:
    """Publica y lee targets físicos sin interpretar scopes de negocio."""

    def __init__(
        self,
        *,
        root: str | Path,
        write_options: ParquetWriteOptions | None = None,
        orphan_grace: timedelta = timedelta(minutes=10),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(root, str | Path) or not str(root).strip():
            raise ParquetValidationError('root must be a non-empty filesystem path')
        if not isinstance(orphan_grace, timedelta) or orphan_grace.total_seconds() < 0:
            raise ParquetValidationError('orphan_grace must be a non-negative timedelta')
        if write_options is not None and not isinstance(write_options, ParquetWriteOptions):
            raise ParquetValidationError('write_options must be ParquetWriteOptions or None')
        self._root = Path(root)
        self._write_options = write_options or ParquetWriteOptions()
        self._orphan_grace = orphan_grace
        self._clock = clock or _utc_now
        self._write_lock = threading.RLock()

    @property
    def root(self) -> Path:
        """Raíz física bajo la cual se anexan los segmentos lógicos validados."""

        return self._root

    def path_for(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
    ) -> Path:
        """Resuelve un target validado sin descubrir contenido del filesystem."""

        definition.validate_target(target)
        return self._root.joinpath(*definition.resolve_route_segments(target))

    def replace(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        table: pa.Table,
    ) -> DatasetPublicationResult:
        """Reemplaza un artefacto completo mediante un commit atómico."""

        started = monotonic()
        self._require_layout(
            definition=definition,
            target=target,
            layout_type=SingleArtifactLayout,
            operation='replace',
        )
        self._validate_table(table)
        if table.num_rows == 0:
            return DatasetPublicationResult.skipped_empty(
                target=target,
                finished_at_utc=self._resolve_now(),
                duration_ms=_elapsed_ms(started),
            )
        try:
            current = self.read(definition=definition, target=target)
        except ParquetPublicationNotFoundError:
            current = None
        if current is not None and table.equals(current.table, check_metadata=True):
            publication = _resolve_publication(
                definition=definition,
                target=target,
                target_path=self.path_for(definition=definition, target=target),
            )
            artifact = publication.artifacts[0]
            return self._publication_result(
                target=target,
                status=PublicationStatus.UNCHANGED,
                item_count=artifact.item_count,
                artifact_count=1,
                size_bytes=artifact.size_bytes,
                content_signature=_read_file_signature(artifact.path),
                started=started,
            )
        artifact = self._replace_table(
            definition=definition,
            target=target,
            table=table,
        )
        return self._publication_result(
            target=target,
            status=PublicationStatus.COMMITTED,
            item_count=artifact.item_count,
            artifact_count=1,
            size_bytes=artifact.size_bytes,
            content_signature=artifact.content_signature,
            started=started,
        )

    def merge(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        incoming: pa.Table,
        key_columns: Iterable[str],
        order_by: Iterable[str] = (),
    ) -> DatasetPublicationResult:
        """Integra por clave haciendo que la fila entrante gane completa, incluidos nulos."""

        started = monotonic()
        self._require_layout(
            definition=definition,
            target=target,
            layout_type=SingleArtifactLayout,
            operation='merge',
        )
        self._validate_table(incoming)
        keys = _normalize_columns(key_columns, field='key_columns', allow_empty=False)
        ordering = _normalize_columns(order_by, field='order_by', allow_empty=True)
        self._validate_merge_columns(incoming, keys=keys, ordering=ordering)
        if incoming.num_rows == 0:
            return DatasetPublicationResult.skipped_empty(
                target=target,
                finished_at_utc=self._resolve_now(),
                duration_ms=_elapsed_ms(started),
            )
        with self._write_lock:
            try:
                current = self.read(definition=definition, target=target)
            except ParquetPublicationNotFoundError:
                current = None
            if current is None:
                merged = self._deduplicate(incoming, keys=keys, ordering=ordering)
            else:
                aligned_current = self._align_table(
                    table=current.table,
                    schema=incoming.schema,
                    context='current publication',
                )
                self._validate_key_values(aligned_current, keys=keys)
                combined = pa.concat_tables((aligned_current, incoming))
                merged = self._deduplicate(combined, keys=keys, ordering=ordering)
                if merged.equals(current.table, check_metadata=True):
                    publication = _resolve_publication(
                        definition=definition,
                        target=target,
                        target_path=self.path_for(definition=definition, target=target),
                    )
                    artifact = publication.artifacts[0]
                    return self._publication_result(
                        target=target,
                        status=PublicationStatus.UNCHANGED,
                        item_count=artifact.item_count,
                        artifact_count=1,
                        size_bytes=artifact.size_bytes,
                        content_signature=_read_file_signature(artifact.path),
                        started=started,
                    )
            artifact = self._replace_table(
                definition=definition,
                target=target,
                table=merged,
            )
        return self._publication_result(
            target=target,
            status=PublicationStatus.COMMITTED,
            item_count=artifact.item_count,
            artifact_count=1,
            size_bytes=artifact.size_bytes,
            content_signature=artifact.content_signature,
            started=started,
        )

    def publish_parts(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        incoming_parts: Iterable[ParquetPart] = (),
        remove_parts: Iterable[DatasetPartKey] = (),
    ) -> DatasetPublicationResult:
        """Confirma un conjunto incremental de partes planas mediante ``current.json``."""

        started = monotonic()
        layout = self._require_layout(
            definition=definition,
            target=target,
            layout_type=FileSetLayout,
            operation='publish_parts',
        )
        incoming = self._normalize_incoming_parts(
            target=target,
            part_dimension=layout.part_dimension,
            values=incoming_parts,
        )
        removals = self._normalize_remove_parts(
            target=target,
            part_dimension=layout.part_dimension,
            values=remove_parts,
        )
        overlap = set(incoming) & removals
        if overlap:
            raise ParquetValidationError(
                f'parts cannot be incoming and removed in the same publication: {sorted(overlap)}'
            )
        for part in incoming.values():
            self._validate_table(part.table)
        if any(part.table.num_rows == 0 for part in incoming.values()):
            return DatasetPublicationResult.skipped_empty(
                target=target,
                finished_at_utc=self._resolve_now(),
                duration_ms=_elapsed_ms(started),
            )
        target_path = self.path_for(definition=definition, target=target)
        with self._write_lock:
            current = _read_manifest(
                target_path=target_path,
                target=target,
                part_dimension=layout.part_dimension,
                missing_ok=True,
            )
            if current is None and not incoming:
                raise ParquetValidationError(
                    'publish_parts requires incoming parts for a new target'
                )
            if not incoming and not removals:
                assert current is not None
                return self._manifest_result(
                    manifest=current,
                    target=target,
                    status=PublicationStatus.UNCHANGED,
                    started=started,
                )
            self._cleanup_locked(
                target_path=target_path,
                target=target,
                part_dimension=layout.part_dimension,
                current=current,
            )
            schema = self._resolve_incoming_schema(incoming=incoming, current=current)
            next_parts = {} if current is None else {part.value: part for part in current.parts}
            for value in removals:
                next_parts.pop(value, None)
            try:
                for value, part in incoming.items():
                    manifest_part, _ = self._write_content_part(
                        target_path=target_path,
                        part_dimension=layout.part_dimension,
                        part_value=value,
                        table=part.table,
                    )
                    next_parts[value] = manifest_part
                if not next_parts:
                    raise ParquetValidationError(
                        'a file-set publication must contain at least one part'
                    )
                parts = tuple(sorted(next_parts.values(), key=lambda item: item.value))
                _validate_preserved_parts(
                    target_path=target_path,
                    schema=schema,
                    parts=parts,
                )
                schema_signature = _schema_signature(schema)
                content_signature = _publication_signature(
                    schema_signature=schema_signature,
                    parts=parts,
                )
                if (
                    current is not None
                    and current.content_signature == content_signature
                    and current.schema.equals(schema, check_metadata=True)
                ):
                    return self._manifest_result(
                        manifest=current,
                        target=target,
                        status=PublicationStatus.UNCHANGED,
                        started=started,
                    )
                manifest = _Manifest(
                    publication_token=uuid4().hex,
                    target=target.identifier,
                    committed_at_utc=self._resolve_now(),
                    part_dimension=layout.part_dimension,
                    item_count=sum(part.item_count for part in parts),
                    content_signature=content_signature,
                    schema=schema,
                    schema_signature=schema_signature,
                    parts=parts,
                )
                self._replace_manifest(target_path=target_path, manifest=manifest)
            except ParquetValidationError:
                raise
            except (OSError, pa.ArrowException) as error:
                raise ParquetWriteError(
                    f'could not publish parquet parts for {target.identifier}'
                ) from error
        return self._manifest_result(
            manifest=manifest,
            target=target,
            status=PublicationStatus.COMMITTED,
            started=started,
        )

    # Para SingleArtifact se abre metadata Parquet y no se materializan row groups en memoria.
    def read_schema(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
    ) -> pa.Schema:
        """Lee solamente el schema confirmado de un target."""

        return _resolve_publication(
            definition=definition,
            target=target,
            target_path=self.path_for(definition=definition, target=target),
        ).schema

    def read(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
    ) -> ParquetReadResult:
        """Lee completa y exclusivamente la publicación vigente de un target."""

        return self.scan(definition=definition, targets=(target,))

    def scan(
        self,
        *,
        definition: DatasetDefinition,
        targets: Iterable[DatasetTarget],
        columns: Iterable[str] | None = None,
        filters: Iterable[ColumnFilter] = (),
    ) -> ParquetReadResult:
        """Proyecta y filtra una o varias particiones explícitas con pushdown Parquet."""

        resolved_targets = _normalize_targets(targets)
        projected_columns = (
            None
            if columns is None
            else _normalize_columns(columns, field='columns', allow_empty=False)
        )
        if len(resolved_targets) > 1 and projected_columns is None:
            raise ParquetValidationError('columns must be explicit when scanning multiple targets')
        resolved_filters = tuple(filters)
        if not all(isinstance(item, ColumnFilter) for item in resolved_filters):
            raise ParquetValidationError('filters must contain only ColumnFilter values')
        publications = tuple(
            _resolve_publication(
                definition=definition,
                target=target,
                target_path=self.path_for(definition=definition, target=target),
                filters=resolved_filters,
            )
            for target in resolved_targets
        )
        # La fachada valida y resuelve publicaciones; el módulo interno ejecuta la consulta.
        return _scan_publications(
            targets=resolved_targets,
            publications=publications,
            columns=projected_columns,
            filters=resolved_filters,
        )

    def cleanup(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
    ) -> ParquetCleanupResult:
        """Elimina temporales y partes huérfanas propias después de la gracia configurada."""

        materialization = definition.get_materialization(target.materialization)
        definition.validate_target(target)
        target_path = self.path_for(definition=definition, target=target)
        with self._write_lock:
            current = None
            part_dimension = None
            if isinstance(materialization.layout, FileSetLayout):
                part_dimension = materialization.layout.part_dimension
                current = _read_manifest(
                    target_path=target_path,
                    target=target,
                    part_dimension=part_dimension,
                    missing_ok=True,
                )
            return self._cleanup_locked(
                target_path=target_path,
                target=target,
                part_dimension=part_dimension,
                current=current,
            )

    def _replace_table(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        table: pa.Table,
    ) -> _Artifact:
        target_path = self.path_for(definition=definition, target=target)
        final_path = target_path / 'data.parquet'
        temporary_path: Path | None = None
        with self._write_lock:
            self._cleanup_locked(
                target_path=target_path,
                target=target,
                part_dimension=None,
                current=None,
            )
            try:
                target_path.mkdir(parents=True, exist_ok=True)
                temporary_path = target_path / f'.data.parquet.{uuid4().hex}.tmp'
                self._write_and_validate_table(path=temporary_path, table=table)
                size_bytes = temporary_path.stat().st_size
                content_signature = _file_signature(temporary_path)
                os.replace(temporary_path, final_path)
                _fsync_directory(target_path)
            except ParquetValidationError:
                raise
            except (OSError, pa.ArrowException) as error:
                raise ParquetWriteError(
                    f'could not replace parquet publication {target.identifier}'
                ) from error
            finally:
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

    def _write_content_part(
        self,
        *,
        target_path: Path,
        part_dimension: str,
        part_value: str,
        table: pa.Table,
    ) -> tuple[_ManifestPart, bool]:
        name_template = f'{part_dimension}={part_value}--{"0" * 64}.parquet'
        if len(os.fsencode(name_template)) > 255:
            raise ParquetValidationError('part identity produces a filename longer than 255 bytes')
        target_path.mkdir(parents=True, exist_ok=True)
        temporary_path = target_path / (f'.{part_dimension}={part_value}.{uuid4().hex}.tmp')
        try:
            self._write_and_validate_table(path=temporary_path, table=table)
            size_bytes = temporary_path.stat().st_size
            content_signature = _file_signature(temporary_path)
            digest = content_signature.removeprefix('sha256:')
            file_name = f'{part_dimension}={part_value}--{digest}.parquet'
            final_path = target_path / file_name
            was_created = False
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

    def _write_and_validate_table(self, *, path: Path, table: pa.Table) -> None:
        options = self._write_options
        pq.write_table(
            table,
            path,
            compression=options.compression,
            compression_level=options.compression_level,
            use_dictionary=options.use_dictionary,
            write_statistics=options.write_statistics,
            row_group_size=options.row_group_size,
        )
        with path.open('rb') as file_handle:
            os.fsync(file_handle.fileno())
        parquet_file = pq.ParquetFile(path)
        if parquet_file.metadata.num_rows != table.num_rows:
            raise ParquetWriteError('written parquet row count does not match the source table')
        if not parquet_file.schema_arrow.equals(table.schema, check_metadata=True):
            raise ParquetSchemaError('written parquet schema does not match the source table')

    def _replace_manifest(self, *, target_path: Path, manifest: _Manifest) -> None:
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

    def _resolve_incoming_schema(
        self,
        *,
        incoming: dict[str, ParquetPart],
        current: _Manifest | None,
    ) -> pa.Schema:
        if not incoming:
            assert current is not None
            return current.schema
        first = next(iter(incoming.values())).table.schema
        self._validate_schema(first)
        for part in incoming.values():
            if not part.table.schema.equals(first, check_metadata=True):
                raise ParquetSchemaError(
                    'all incoming parts must use the same authoritative schema'
                )
        return first

    def _validate_table(self, table: pa.Table) -> None:
        _validate_table_impl(table)

    def _validate_schema(self, schema: pa.Schema) -> None:
        _validate_schema_impl(schema)

    def _validate_merge_columns(
        self,
        table: pa.Table,
        *,
        keys: tuple[str, ...],
        ordering: tuple[str, ...],
    ) -> None:
        _validate_merge_columns_impl(table, keys=keys, ordering=ordering)

    def _validate_key_values(self, table: pa.Table, *, keys: tuple[str, ...]) -> None:
        _validate_key_values_impl(table, keys=keys)

    def _align_table(
        self,
        *,
        table: pa.Table,
        schema: pa.Schema,
        context: str,
    ) -> pa.Table:
        return _align_table_impl(table=table, schema=schema, context=context)

    def _deduplicate(
        self,
        table: pa.Table,
        *,
        keys: tuple[str, ...],
        ordering: tuple[str, ...],
    ) -> pa.Table:
        self._validate_key_values(table, keys=keys)
        internal = '__atlanticus_merge_order'
        while internal in table.column_names:
            internal = f'_{internal}'
        with_order = table.append_column(
            internal,
            pa.array(range(table.num_rows), type=pa.int64()),
        )
        grouped = with_order.group_by(list(keys), use_threads=False).aggregate([(internal, 'max')])
        selected_column = f'{internal}_max'
        selected = grouped[selected_column]
        selected = pc.sort_indices(selected)
        deduplicated = table.take(grouped[selected_column].take(selected))
        if ordering:
            indices = pc.sort_indices(
                deduplicated,
                sort_keys=[(column, 'ascending') for column in ordering],
            )
            deduplicated = deduplicated.take(indices)
        return deduplicated

    def _normalize_incoming_parts(
        self,
        *,
        target: DatasetTarget,
        part_dimension: str,
        values: Iterable[ParquetPart],
    ) -> dict[str, ParquetPart]:
        return _normalize_incoming_parts_impl(
            target=target,
            part_dimension=part_dimension,
            values=values,
        )

    def _normalize_remove_parts(
        self,
        *,
        target: DatasetTarget,
        part_dimension: str,
        values: Iterable[DatasetPartKey],
    ) -> set[str]:
        return _normalize_remove_parts_impl(
            target=target,
            part_dimension=part_dimension,
            values=values,
        )

    def _require_layout(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        layout_type: type[SingleArtifactLayout] | type[FileSetLayout],
        operation: str,
    ) -> SingleArtifactLayout | FileSetLayout:
        definition.validate_target(target)
        materialization = definition.get_materialization(target.materialization)
        if not isinstance(materialization.layout, layout_type):
            raise ParquetLayoutError(
                f'{operation} is not supported for {type(materialization.layout).__name__}'
            )
        return materialization.layout

    def _cleanup_locked(
        self,
        *,
        target_path: Path,
        target: DatasetTarget,
        part_dimension: str | None,
        current: _Manifest | None,
    ) -> ParquetCleanupResult:
        if not target_path.exists():
            return ParquetCleanupResult(
                target=target,
                temporary_count=0,
                orphan_part_count=0,
                reclaimed_bytes=0,
            )
        now = self._resolve_now().timestamp()
        referenced = set() if current is None else {part.path for part in current.parts}
        temporary_count = 0
        orphan_part_count = 0
        reclaimed_bytes = 0
        try:
            candidates = tuple(target_path.iterdir())
        except OSError as error:
            raise ParquetWriteError('could not inspect parquet target for cleanup') from error
        for candidate in candidates:
            is_temporary = candidate.is_file() and _is_temporary_filename(candidate.name)
            is_orphan_part = (
                part_dimension is not None
                and candidate.is_file()
                and candidate.name not in referenced
                and _is_owned_part_filename(candidate.name, part_dimension=part_dimension)
            )
            if not is_temporary and not is_orphan_part:
                continue
            try:
                stat = candidate.stat()
                age_seconds = max(0.0, now - stat.st_mtime)
                if age_seconds < self._orphan_grace.total_seconds():
                    continue
                candidate.unlink()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise ParquetWriteError(
                    f'could not remove orphan parquet artifact: {candidate.name}'
                ) from error
            reclaimed_bytes += stat.st_size
            if is_temporary:
                temporary_count += 1
            else:
                orphan_part_count += 1
        return ParquetCleanupResult(
            target=target,
            temporary_count=temporary_count,
            orphan_part_count=orphan_part_count,
            reclaimed_bytes=reclaimed_bytes,
        )

    def _resolve_now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise ParquetValidationError('clock must return a datetime')
        if value.tzinfo is None or value.utcoffset() is None:
            raise ParquetValidationError('clock must return a timezone-aware datetime')
        return value.astimezone(UTC)

    def _publication_result(
        self,
        *,
        target: DatasetTarget,
        status: PublicationStatus,
        item_count: int,
        artifact_count: int,
        size_bytes: int,
        content_signature: str | None,
        started: float,
    ) -> DatasetPublicationResult:
        return DatasetPublicationResult(
            target=target,
            status=status,
            quality=PublicationQuality.SUCCESS,
            finished_at_utc=self._resolve_now(),
            duration_ms=_elapsed_ms(started),
            item_count=item_count,
            artifact_count=artifact_count,
            size_bytes=size_bytes,
            content_signature=content_signature,
        )

    def _manifest_result(
        self,
        *,
        manifest: _Manifest,
        target: DatasetTarget,
        status: PublicationStatus,
        started: float,
    ) -> DatasetPublicationResult:
        return self._publication_result(
            target=target,
            status=status,
            item_count=manifest.item_count,
            artifact_count=len(manifest.parts),
            size_bytes=sum(part.size_bytes for part in manifest.parts),
            content_signature=manifest.content_signature,
            started=started,
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _elapsed_ms(started: float) -> float:
    return round(max(0.0, (monotonic() - started) * 1000), 3)
