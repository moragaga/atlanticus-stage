"""Resolución e inspección de publicaciones Parquet confirmadas."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from atlanticus.datasets.layouts import SingleArtifactLayout
from atlanticus.datasets.models import DatasetDefinition, DatasetTarget
from atlanticus.datasets.parquet._filesystem import (
    _file_signature,
    _validate_part_filename,
)
from atlanticus.datasets.parquet._validation import _validate_schema
from atlanticus.datasets.parquet.errors import (
    ParquetCorruptionError,
    ParquetPublicationNotFoundError,
    ParquetReadError,
    ParquetSchemaError,
)
from atlanticus.datasets.parquet.manifest import (
    _decode_manifest,
    _Manifest,
    _ManifestPart,
)
from atlanticus.datasets.parquet.models import (
    ColumnFilter,
    FilterOperator,
    _Artifact,
    _ResolvedPublication,
)


def _resolve_publication(
    *,
    definition: DatasetDefinition,
    target: DatasetTarget,
    target_path: Path,
    filters: tuple[ColumnFilter, ...] = (),
) -> _ResolvedPublication:
    materialization = definition.get_materialization(target.materialization)
    definition.validate_target(target)
    if isinstance(materialization.layout, SingleArtifactLayout):
        path = target_path / 'data.parquet'
        artifact = _inspect_artifact(path=path, missing_is_publication=True)
        return _ResolvedPublication(
            target=target,
            schema=artifact.schema,
            artifacts=(artifact,),
        )
    manifest = _read_manifest(
        target_path=target_path,
        target=target,
        part_dimension=materialization.layout.part_dimension,
        missing_ok=False,
    )
    assert manifest is not None
    for part in manifest.parts:
        _validate_part_filename(
            part=part,
            part_dimension=manifest.part_dimension,
        )
    selected_parts = _select_manifest_parts(
        manifest=manifest,
        filters=filters,
    )
    artifacts: list[_Artifact] = []
    for part in selected_parts:
        artifact = _inspect_artifact(
            path=target_path / part.path,
            missing_is_publication=False,
            expected_item_count=part.item_count,
            expected_size_bytes=part.size_bytes,
            content_signature=part.content_signature,
            part_value=part.value,
        )
        try:
            _validate_physical_schema(
                physical=artifact.schema,
                logical=manifest.schema,
                context=f'part {part.value}',
            )
        except ParquetSchemaError as error:
            raise ParquetCorruptionError(
                f'parquet part schema does not match current manifest: {part.path}'
            ) from error
        artifacts.append(artifact)
    return _ResolvedPublication(
        target=target,
        schema=manifest.schema,
        artifacts=tuple(artifacts),
        publication_token=manifest.publication_token,
        part_dimension=manifest.part_dimension,
    )


def _inspect_artifact(
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
        raise ParquetCorruptionError(f'parquet artifact cannot be opened: {path.name}') from error
    if item_count < 1:
        raise ParquetCorruptionError(f'confirmed parquet artifact is empty: {path.name}')
    if expected_item_count is not None and item_count != expected_item_count:
        raise ParquetCorruptionError(
            f'parquet part row count does not match current manifest: {path.name}'
        )
    try:
        _validate_schema(schema)
    except ParquetSchemaError as error:
        raise ParquetCorruptionError(f'parquet artifact schema is invalid: {path.name}') from error
    return _Artifact(
        path=path,
        schema=schema,
        item_count=item_count,
        size_bytes=size_bytes,
        content_signature=content_signature,
        part_value=part_value,
    )


def _read_manifest(
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


def _select_manifest_parts(
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
                part for part in parts if _part_filter_matches(part_value=part.value, item=item)
            )
    return parts


def _validate_preserved_parts(
    *,
    target_path: Path,
    schema: pa.Schema,
    parts: tuple[_ManifestPart, ...],
) -> None:
    for part in parts:
        artifact = _inspect_artifact(
            path=target_path / part.path,
            missing_is_publication=False,
            expected_item_count=part.item_count,
            expected_size_bytes=part.size_bytes,
            content_signature=part.content_signature,
            part_value=part.value,
        )
        _validate_physical_schema(
            physical=artifact.schema,
            logical=schema,
            context=f'part {part.value}',
        )


def _validate_physical_schema(
    *,
    physical: pa.Schema,
    logical: pa.Schema,
    context: str,
) -> None:
    _validate_schema(physical)
    _validate_schema(logical)
    for field in logical:
        if field.name not in physical.names:
            continue
        physical_field = physical.field(field.name)
        if physical_field.type != field.type:
            raise ParquetSchemaError(
                f'{context} has incompatible type for column {field.name}: '
                f'{physical_field.type} != {field.type}'
            )


def _read_file_signature(path: Path) -> str:
    try:
        return _file_signature(path)
    except OSError as error:
        raise ParquetReadError(f'could not read parquet artifact: {path.name}') from error


def _part_filter_matches(*, part_value: str, item: ColumnFilter) -> bool:
    if item.operator is FilterOperator.EQUAL:
        return part_value == str(item.value)
    return part_value in {str(value) for value in item.value}
