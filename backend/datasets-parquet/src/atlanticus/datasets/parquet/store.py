"""Store Parquet atómico basado exclusivamente en tablas PyArrow."""

from __future__ import annotations

import hashlib
import os
import re
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from atlanticus.datasets import (
    DatasetDefinition,
    DatasetPartKey,
    DatasetPublicationResult,
    DatasetTarget,
    FileSetLayout,
    PublicationQuality,
    PublicationStatus,
    SingleArtifactLayout,
)
from atlanticus.datasets.parquet.errors import (
    ParquetCorruptionError,
    ParquetLayoutError,
    ParquetPublicationNotFoundError,
    ParquetReadError,
    ParquetSchemaError,
    ParquetValidationError,
    ParquetWriteError,
)
from atlanticus.datasets.parquet.manifest import (
    _decode_manifest,
    _encode_manifest,
    _Manifest,
    _ManifestPart,
    _publication_signature,
    _schema_signature,
)
from atlanticus.datasets.parquet.models import (
    ColumnFilter,
    FilterOperator,
    ParquetCleanupResult,
    ParquetPart,
    ParquetReadResult,
    ParquetWriteOptions,
    _Artifact,
    _ResolvedPublication,
)

_TEMPORARY_PATTERN = re.compile(r'^\..+\.[0-9a-f]{32}\.tmp$')
_PART_SIGNATURE_PATTERN = re.compile(r'^[0-9a-f]{64}$')
_FILTER_OPERATORS = {
    FilterOperator.EQUAL: '=',
    FilterOperator.NOT_EQUAL: '!=',
    FilterOperator.GREATER_THAN: '>',
    FilterOperator.GREATER_THAN_OR_EQUAL: '>=',
    FilterOperator.LESS_THAN: '<',
    FilterOperator.LESS_THAN_OR_EQUAL: '<=',
    FilterOperator.IN: 'in',
}


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
            publication = self._resolve_publication(
                definition=definition,
                target=target,
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
                    publication = self._resolve_publication(
                        definition=definition,
                        target=target,
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
            current = self._read_manifest(
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
                self._validate_preserved_parts(
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
            self._resolve_publication(
                definition=definition,
                target=target,
                filters=resolved_filters,
            )
            for target in resolved_targets
        )
        output_schema = self._resolve_scan_schema(
            publications=publications,
            columns=projected_columns,
        )
        filter_fields = self._resolve_filter_fields(
            publications=publications,
            filters=resolved_filters,
        )
        tables: list[pa.Table] = []
        artifact_count = 0
        size_bytes = 0
        warnings: list[str] = []
        publication_tokens: list[str] = []
        for publication in publications:
            selected, residual_filters = self._select_artifacts(
                publication=publication,
                filters=resolved_filters,
            )
            if publication.publication_token is not None:
                publication_tokens.append(publication.publication_token)
            for artifact in selected:
                table, artifact_warnings = self._scan_artifact(
                    publication=publication,
                    artifact=artifact,
                    output_schema=output_schema,
                    filters=residual_filters,
                    filter_fields=filter_fields,
                )
                tables.append(table)
                warnings.extend(artifact_warnings)
                artifact_count += 1
                size_bytes += artifact.size_bytes
        result_table = (
            pa.concat_tables(tables)
            if tables
            else pa.Table.from_arrays(
                [pa.array([], type=field.type) for field in output_schema],
                schema=output_schema,
            )
        )
        return ParquetReadResult(
            table=result_table,
            targets=resolved_targets,
            artifact_count=artifact_count,
            size_bytes=size_bytes,
            publication_tokens=tuple(publication_tokens),
            warnings=tuple(dict.fromkeys(warnings)),
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
                current = self._read_manifest(
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

    def _resolve_publication(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        filters: tuple[ColumnFilter, ...] = (),
    ) -> _ResolvedPublication:
        materialization = definition.get_materialization(target.materialization)
        definition.validate_target(target)
        target_path = self.path_for(definition=definition, target=target)
        if isinstance(materialization.layout, SingleArtifactLayout):
            path = target_path / 'data.parquet'
            artifact = self._inspect_artifact(path=path, missing_is_publication=True)
            return _ResolvedPublication(
                target=target,
                schema=artifact.schema,
                artifacts=(artifact,),
            )
        manifest = self._read_manifest(
            target_path=target_path,
            target=target,
            part_dimension=materialization.layout.part_dimension,
            missing_ok=False,
        )
        assert manifest is not None
        for part in manifest.parts:
            self._validate_part_filename(
                part=part,
                part_dimension=manifest.part_dimension,
            )
        selected_parts = self._select_manifest_parts(
            manifest=manifest,
            filters=filters,
        )
        artifacts: list[_Artifact] = []
        for part in selected_parts:
            artifact = self._inspect_artifact(
                path=target_path / part.path,
                missing_is_publication=False,
                expected_item_count=part.item_count,
                expected_size_bytes=part.size_bytes,
                content_signature=part.content_signature,
                part_value=part.value,
            )
            self._validate_physical_schema(
                physical=artifact.schema,
                logical=manifest.schema,
                context=f'part {part.value}',
            )
            artifacts.append(artifact)
        return _ResolvedPublication(
            target=target,
            schema=manifest.schema,
            artifacts=tuple(artifacts),
            publication_token=manifest.publication_token,
            part_dimension=manifest.part_dimension,
        )

    def _inspect_artifact(
        self,
        *,
        path: Path,
        missing_is_publication: bool,
        expected_item_count: int | None = None,
        expected_size_bytes: int | None = None,
        content_signature: str | None = None,
        part_value: str | None = None,
    ) -> _Artifact:
        try:
            size_bytes = path.stat().st_size
        except FileNotFoundError as error:
            if missing_is_publication:
                raise ParquetPublicationNotFoundError(
                    f'parquet publication does not exist: {path}'
                ) from error
            raise ParquetCorruptionError(
                f'current manifest references a missing parquet part: {path.name}'
            ) from error
        except OSError as error:
            raise ParquetReadError(f'could not inspect parquet artifact: {path.name}') from error
        if expected_size_bytes is not None and size_bytes != expected_size_bytes:
            raise ParquetCorruptionError(
                f'parquet part size does not match current manifest: {path.name}'
            )
        if content_signature is not None and _read_file_signature(path) != content_signature:
            raise ParquetCorruptionError(
                f'parquet part signature does not match current manifest: {path.name}'
            )
        try:
            parquet_file = pq.ParquetFile(path)
            schema = parquet_file.schema_arrow
            item_count = parquet_file.metadata.num_rows
        except (OSError, pa.ArrowException) as error:
            raise ParquetCorruptionError(
                f'parquet artifact cannot be opened: {path.name}'
            ) from error
        if item_count < 1:
            raise ParquetCorruptionError(f'confirmed parquet artifact is empty: {path.name}')
        if expected_item_count is not None and item_count != expected_item_count:
            raise ParquetCorruptionError(
                f'parquet part row count does not match current manifest: {path.name}'
            )
        self._validate_schema(schema)
        return _Artifact(
            path=path,
            schema=schema,
            item_count=item_count,
            size_bytes=size_bytes,
            content_signature=content_signature,
            part_value=part_value,
        )

    def _read_manifest(
        self,
        *,
        target_path: Path,
        target: DatasetTarget,
        part_dimension: str,
        missing_ok: bool,
    ) -> _Manifest | None:
        path = target_path / 'current.json'
        try:
            content = path.read_bytes()
        except FileNotFoundError as error:
            if missing_ok:
                return None
            raise ParquetPublicationNotFoundError(
                f'parquet publication does not exist: {path}'
            ) from error
        except OSError as error:
            raise ParquetReadError('could not read parquet current manifest') from error
        return _decode_manifest(
            content,
            expected_target=target.identifier,
            expected_part_dimension=part_dimension,
        )

    def _scan_artifact(
        self,
        *,
        publication: _ResolvedPublication,
        artifact: _Artifact,
        output_schema: pa.Schema,
        filters: tuple[ColumnFilter, ...],
        filter_fields: dict[str, pa.Field],
    ) -> tuple[pa.Table, tuple[str, ...]]:
        physical_names = set(artifact.schema.names)
        for item in filters:
            if item.column not in physical_names:
                return _empty_table(output_schema), ()
        read_columns = [field.name for field in output_schema if field.name in physical_names]
        sentinel: str | None = None
        if not read_columns:
            sentinel = artifact.schema.names[0]
            read_columns.append(sentinel)
        parquet_filters = [
            self._to_parquet_filter(item=item, field=filter_fields[item.column]) for item in filters
        ]
        try:
            table = pq.read_table(
                artifact.path,
                columns=read_columns,
                filters=parquet_filters or None,
            )
        except (OSError, pa.ArrowException) as error:
            raise ParquetReadError(
                f'could not scan parquet artifact: {artifact.path.name}'
            ) from error
        if sentinel is not None:
            table = table.drop((sentinel,))
        arrays: list[pa.ChunkedArray | pa.Array] = []
        warnings: list[str] = []
        for field in output_schema:
            if field.name in table.column_names:
                column = table[field.name]
                if column.type != field.type:
                    raise ParquetSchemaError(
                        f'incompatible type for column {field.name}: {column.type} != {field.type}'
                    )
                arrays.append(column)
            else:
                arrays.append(pa.nulls(table.num_rows, type=field.type))
                part = '' if artifact.part_value is None else f' part={artifact.part_value}'
                warnings.append(
                    f'column {field.name} is absent from {publication.target.identifier}{part}; '
                    'null values were projected'
                )
        return pa.Table.from_arrays(arrays, schema=output_schema), tuple(warnings)

    def _resolve_scan_schema(
        self,
        *,
        publications: tuple[_ResolvedPublication, ...],
        columns: tuple[str, ...] | None,
    ) -> pa.Schema:
        if columns is None:
            return publications[0].schema
        fields = [
            self._resolve_field(publications=publications, column=column, required=True)
            for column in columns
        ]
        return pa.schema(fields)

    def _resolve_filter_fields(
        self,
        *,
        publications: tuple[_ResolvedPublication, ...],
        filters: tuple[ColumnFilter, ...],
    ) -> dict[str, pa.Field]:
        fields: dict[str, pa.Field] = {}
        for item in filters:
            if item.column in fields:
                continue
            field = self._resolve_field(
                publications=publications,
                column=item.column,
                required=False,
            )
            if field is None:
                part_only = all(
                    publication.part_dimension == item.column
                    and item.operator in {FilterOperator.EQUAL, FilterOperator.IN}
                    for publication in publications
                )
                if not part_only:
                    raise ParquetSchemaError(
                        f'filter column does not exist in the requested publications: {item.column}'
                    )
            else:
                fields[item.column] = field
        return fields

    def _resolve_field(
        self,
        *,
        publications: tuple[_ResolvedPublication, ...],
        column: str,
        required: bool,
    ) -> pa.Field | None:
        found = [
            publication.schema.field(column)
            for publication in publications
            if column in publication.schema.names
        ]
        if not found:
            if required:
                raise ParquetSchemaError(
                    f'column does not exist in the requested publications: {column}'
                )
            return None
        expected_type = found[-1].type
        if any(field.type != expected_type for field in found):
            types = sorted({str(field.type) for field in found})
            raise ParquetSchemaError(f'incompatible types for column {column}: {types}')
        nullable = len(found) != len(publications) or any(field.nullable for field in found)
        authoritative = found[-1]
        return pa.field(
            column,
            authoritative.type,
            nullable=nullable,
            metadata=authoritative.metadata,
        )

    def _select_manifest_parts(
        self,
        *,
        manifest: _Manifest,
        filters: tuple[ColumnFilter, ...],
    ) -> tuple[_ManifestPart, ...]:
        parts = manifest.parts
        for item in filters:
            if item.column == manifest.part_dimension and item.operator in {
                FilterOperator.EQUAL,
                FilterOperator.IN,
            }:
                parts = tuple(
                    part
                    for part in parts
                    if _part_filter_matches(part_value=part.value, item=item)
                )
        return parts

    def _select_artifacts(
        self,
        *,
        publication: _ResolvedPublication,
        filters: tuple[ColumnFilter, ...],
    ) -> tuple[tuple[_Artifact, ...], tuple[ColumnFilter, ...]]:
        artifacts = publication.artifacts
        residual: list[ColumnFilter] = []
        for item in filters:
            if item.column == publication.part_dimension and item.operator in {
                FilterOperator.EQUAL,
                FilterOperator.IN,
            }:
                artifacts = tuple(
                    artifact
                    for artifact in artifacts
                    if artifact.part_value is not None
                    and _part_filter_matches(part_value=artifact.part_value, item=item)
                )
            else:
                residual.append(item)
        return artifacts, tuple(residual)

    def _to_parquet_filter(self, *, item: ColumnFilter, field: pa.Field) -> tuple[str, str, object]:
        try:
            if item.operator is FilterOperator.IN:
                value = pa.array(item.value, type=field.type).to_pylist()
            else:
                value = pa.scalar(item.value, type=field.type).as_py()
        except (pa.ArrowException, TypeError, ValueError) as error:
            raise ParquetValidationError(
                f'filter value is incompatible with column {item.column} ({field.type})'
            ) from error
        return item.column, _FILTER_OPERATORS[item.operator], value

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

    def _validate_preserved_parts(
        self,
        *,
        target_path: Path,
        schema: pa.Schema,
        parts: tuple[_ManifestPart, ...],
    ) -> None:
        for part in parts:
            artifact = self._inspect_artifact(
                path=target_path / part.path,
                missing_is_publication=False,
                expected_item_count=part.item_count,
                expected_size_bytes=part.size_bytes,
                content_signature=part.content_signature,
                part_value=part.value,
            )
            self._validate_physical_schema(
                physical=artifact.schema,
                logical=schema,
                context=f'part {part.value}',
            )

    def _validate_physical_schema(
        self,
        *,
        physical: pa.Schema,
        logical: pa.Schema,
        context: str,
    ) -> None:
        self._validate_schema(physical)
        self._validate_schema(logical)
        for field in logical:
            if field.name not in physical.names:
                continue
            physical_field = physical.field(field.name)
            if physical_field.type != field.type:
                raise ParquetSchemaError(
                    f'{context} has incompatible type for column {field.name}: '
                    f'{physical_field.type} != {field.type}'
                )

    def _validate_table(self, table: pa.Table) -> None:
        if not isinstance(table, pa.Table):
            raise ParquetValidationError('table must be a pyarrow.Table')
        self._validate_schema(table.schema)
        if table.num_columns == 0:
            raise ParquetValidationError('table must contain at least one column')

    def _validate_schema(self, schema: pa.Schema) -> None:
        if len(schema.names) != len(set(schema.names)):
            raise ParquetSchemaError('schema column names must not contain duplicates')

    def _validate_merge_columns(
        self,
        table: pa.Table,
        *,
        keys: tuple[str, ...],
        ordering: tuple[str, ...],
    ) -> None:
        for column in (*keys, *ordering):
            if column not in table.column_names:
                raise ParquetSchemaError(f'merge column does not exist: {column}')
        self._validate_key_values(table, keys=keys)

    def _validate_key_values(self, table: pa.Table, *, keys: tuple[str, ...]) -> None:
        for column in keys:
            if table[column].null_count:
                raise ParquetValidationError(f'merge key column must not contain nulls: {column}')

    def _align_table(
        self,
        *,
        table: pa.Table,
        schema: pa.Schema,
        context: str,
    ) -> pa.Table:
        arrays: list[pa.ChunkedArray | pa.Array] = []
        for field in schema:
            if field.name not in table.column_names:
                arrays.append(pa.nulls(table.num_rows, type=field.type))
                continue
            column = table[field.name]
            if column.type != field.type:
                raise ParquetSchemaError(
                    f'{context} has incompatible type for column {field.name}: '
                    f'{column.type} != {field.type}'
                )
            arrays.append(column)
        return pa.Table.from_arrays(arrays, schema=schema)

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
        if isinstance(values, str | bytes):
            raise ParquetValidationError('incoming_parts must be an iterable of ParquetPart')
        try:
            parts = tuple(values)
        except TypeError as error:
            raise ParquetValidationError(
                'incoming_parts must be an iterable of ParquetPart'
            ) from error
        if not all(isinstance(item, ParquetPart) for item in parts):
            raise ParquetValidationError('incoming_parts must contain only ParquetPart values')
        normalized: dict[str, ParquetPart] = {}
        for part in parts:
            if part.key.target != target or part.key.dimension != part_dimension:
                raise ParquetValidationError('incoming part does not belong to the target layout')
            if part.key.value in normalized:
                raise ParquetValidationError(f'duplicate incoming part value: {part.key.value}')
            normalized[part.key.value] = part
        return normalized

    def _normalize_remove_parts(
        self,
        *,
        target: DatasetTarget,
        part_dimension: str,
        values: Iterable[DatasetPartKey],
    ) -> set[str]:
        if isinstance(values, str | bytes):
            raise ParquetValidationError('remove_parts must be an iterable of DatasetPartKey')
        try:
            parts = tuple(values)
        except TypeError as error:
            raise ParquetValidationError(
                'remove_parts must be an iterable of DatasetPartKey'
            ) from error
        if not all(isinstance(item, DatasetPartKey) for item in parts):
            raise ParquetValidationError('remove_parts must contain only DatasetPartKey values')
        normalized: set[str] = set()
        for part in parts:
            if part.target != target or part.dimension != part_dimension:
                raise ParquetValidationError('removed part does not belong to the target layout')
            if part.value in normalized:
                raise ParquetValidationError(f'duplicate removed part value: {part.value}')
            normalized.add(part.value)
        return normalized

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

    def _validate_part_filename(
        self,
        *,
        part: _ManifestPart,
        part_dimension: str,
    ) -> None:
        digest = part.content_signature.removeprefix('sha256:')
        if not _PART_SIGNATURE_PATTERN.fullmatch(digest):
            raise ParquetCorruptionError('parquet part signature is invalid')
        expected = f'{part_dimension}={part.value}--{digest}.parquet'
        if part.path != expected:
            raise ParquetCorruptionError(
                f'parquet part filename does not match its identity: {part.path}'
            )

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
            is_temporary = candidate.is_file() and _TEMPORARY_PATTERN.fullmatch(candidate.name)
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


def _normalize_targets(values: Iterable[DatasetTarget]) -> tuple[DatasetTarget, ...]:
    if isinstance(values, DatasetTarget | str | bytes):
        raise ParquetValidationError('targets must be a non-string iterable of DatasetTarget')
    try:
        targets = tuple(values)
    except TypeError as error:
        raise ParquetValidationError('targets must be an iterable of DatasetTarget') from error
    if not targets or not all(isinstance(item, DatasetTarget) for item in targets):
        raise ParquetValidationError('targets must contain at least one DatasetTarget')
    if len(set(targets)) != len(targets):
        raise ParquetValidationError('targets must not contain duplicates')
    return targets


def _normalize_columns(
    values: Iterable[str],
    *,
    field: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if isinstance(values, str | bytes):
        raise ParquetValidationError(f'{field} must be a non-string iterable')
    try:
        columns = tuple(values)
    except TypeError as error:
        raise ParquetValidationError(f'{field} must be an iterable of strings') from error
    if not allow_empty and not columns:
        raise ParquetValidationError(f'{field} must not be empty')
    if not all(isinstance(column, str) and column for column in columns):
        raise ParquetValidationError(f'{field} must contain non-empty strings')
    if len(set(columns)) != len(columns):
        raise ParquetValidationError(f'{field} must not contain duplicates')
    return columns


def _part_filter_matches(*, part_value: str, item: ColumnFilter) -> bool:
    if item.operator is FilterOperator.EQUAL:
        return part_value == str(item.value)
    return part_value in {str(value) for value in item.value}


def _empty_table(schema: pa.Schema) -> pa.Table:
    return pa.Table.from_arrays(
        [pa.array([], type=field.type) for field in schema],
        schema=schema,
    )


def _read_file_signature(path: Path) -> str:
    try:
        return _file_signature(path)
    except OSError as error:
        raise ParquetReadError(f'could not read parquet artifact: {path.name}') from error


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


def _elapsed_ms(started: float) -> float:
    return round(max(0.0, (monotonic() - started) * 1000), 3)
