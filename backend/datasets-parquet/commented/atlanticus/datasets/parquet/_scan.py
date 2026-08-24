# Espejo pedagógico: concentra la mecánica interna de consulta sin exponer una API nueva.
"""Internals de consulta y pushdown para publicaciones Parquet resueltas."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq

from atlanticus.datasets.models import DatasetTarget
from atlanticus.datasets.parquet.errors import (
    ParquetCorruptionError,
    ParquetReadError,
    ParquetSchemaError,
    ParquetValidationError,
)
from atlanticus.datasets.parquet.models import (
    ColumnFilter,
    FilterOperator,
    ParquetReadResult,
    _Artifact,
    _ResolvedPublication,
)

# PyArrow usa operadores textuales para construir el filtro que llega al lector Parquet.
_FILTER_OPERATORS = {
    FilterOperator.EQUAL: '=',
    FilterOperator.NOT_EQUAL: '!=',
    FilterOperator.GREATER_THAN: '>',
    FilterOperator.GREATER_THAN_OR_EQUAL: '>=',
    FilterOperator.LESS_THAN: '<',
    FilterOperator.LESS_THAN_OR_EQUAL: '<=',
    FilterOperator.IN: 'in',
}


def _scan_publications(
    *,
    targets: tuple[DatasetTarget, ...],
    publications: tuple[_ResolvedPublication, ...],
    columns: tuple[str, ...] | None,
    projection_schema: pa.Schema | None,
    filters: tuple[ColumnFilter, ...],
) -> ParquetReadResult:
    # La fachada ya resolvió targets y publicaciones; aquí sólo se ejecuta la consulta.
    output_schema = _resolve_scan_schema(
        publications=publications,
        columns=columns,
        projection_schema=projection_schema,
    )
    filter_fields = _resolve_filter_fields(
        publications=publications,
        filters=filters,
    )
    tables: list[pa.Table] = []
    artifact_count = 0
    size_bytes = 0
    warnings: list[str] = []
    publication_tokens: list[str] = []
    for publication in publications:
        # Los filtros sobre la dimensión física eliminan partes antes de leer Parquet.
        selected, residual_filters = _select_artifacts(
            publication=publication,
            filters=filters,
        )
        if publication.publication_token is not None:
            publication_tokens.append(publication.publication_token)
        for artifact in selected:
            table, artifact_warnings = _scan_artifact(
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
    result_table = pa.concat_tables(tables) if tables else _empty_table(output_schema)
    return ParquetReadResult(
        table=result_table,
        targets=targets,
        artifact_count=artifact_count,
        size_bytes=size_bytes,
        publication_tokens=tuple(publication_tokens),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _scan_artifact(
    *,
    publication: _ResolvedPublication,
    artifact: _Artifact,
    output_schema: pa.Schema,
    filters: tuple[ColumnFilter, ...],
    filter_fields: dict[str, pa.Field],
) -> tuple[pa.Table, tuple[str, ...]]:
    physical_names = set(artifact.schema.names)
    for item in filters:
        # Un artefacto que no contiene la columna de un filtro residual no aporta filas válidas.
        if item.column not in physical_names:
            return _empty_table(output_schema), ()
    read_columns = [field.name for field in output_schema if field.name in physical_names]
    sentinel: str | None = None
    if not read_columns:
        # PyArrow necesita leer al menos una columna para conservar el número de filas.
        sentinel = artifact.schema.names[0]
        read_columns.append(sentinel)
    parquet_filters = [
        _to_parquet_filter(item=item, field=filter_fields[item.column]) for item in filters
    ]
    try:
        table = pq.read_table(
            artifact.path,
            columns=read_columns,
            filters=parquet_filters or None,
        )
    except (OSError, pa.ArrowException) as error:
        raise ParquetReadError(f'could not scan parquet artifact: {artifact.path.name}') from error
    if sentinel is not None:
        table = table.drop((sentinel,))
    arrays: list[pa.ChunkedArray | pa.Array] = []
    warnings: list[str] = []
    for field in output_schema:
        if field.name in table.column_names:
            column = table[field.name]
            if column.type != field.type:
                raise ParquetCorruptionError(
                    f'parquet artifact schema changed while scanning column {field.name}'
                )
            arrays.append(column)
        else:
            # Columnas nuevas pueden faltar en publicaciones antiguas y se proyectan como null.
            arrays.append(pa.nulls(table.num_rows, type=field.type))
            part = '' if artifact.part_value is None else f' part={artifact.part_value}'
            warnings.append(
                f'column {field.name} is absent from {publication.target.identifier}{part}; '
                'null values were projected'
            )
    return pa.Table.from_arrays(arrays, schema=output_schema), tuple(warnings)


def _resolve_scan_schema(
    *,
    publications: tuple[_ResolvedPublication, ...],
    columns: tuple[str, ...] | None,
    projection_schema: pa.Schema | None,
) -> pa.Schema:
    # El schema explícito autoriza a sintetizar columnas ausentes sin perder su tipo.
    if projection_schema is not None:
        fields = [
            _resolve_projected_field(publications=publications, expected=field)
            for field in projection_schema
        ]
        return pa.schema(fields, metadata=projection_schema.metadata)
    if columns is None:
        return publications[0].schema
    fields = [
        _resolve_field(publications=publications, column=column, required=True)
        for column in columns
    ]
    return pa.schema(fields)


def _resolve_projected_field(
    *,
    publications: tuple[_ResolvedPublication, ...],
    expected: pa.Field,
) -> pa.Field:
    # Cuando existe físicamente, el tipo no puede contradecir el contrato tipado.
    found = [
        publication.schema.field(expected.name)
        for publication in publications
        if expected.name in publication.schema.names
    ]
    incompatible = [field.type for field in found if field.type != expected.type]
    if incompatible:
        types = sorted({str(expected.type), *(str(data_type) for data_type in incompatible)})
        raise ParquetSchemaError(f'incompatible type for projected column {expected.name}: {types}')
    # Si falta en cualquier publicación, la salida debe admitir los null sintetizados.
    nullable = (
        expected.nullable
        or len(found) != len(publications)
        or any(field.nullable for field in found)
    )
    return pa.field(
        expected.name,
        expected.type,
        nullable=nullable,
        metadata=expected.metadata,
    )


def _resolve_filter_fields(
    *,
    publications: tuple[_ResolvedPublication, ...],
    filters: tuple[ColumnFilter, ...],
) -> dict[str, pa.Field]:
    fields: dict[str, pa.Field] = {}
    for item in filters:
        if item.column in fields:
            continue
        field = _resolve_field(
            publications=publications,
            column=item.column,
            required=False,
        )
        if field is None:
            # Una dimensión de partes puede filtrar sin existir como columna física del Parquet.
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
    # Una columna ausente en alguna publicación vuelve nullable el schema combinado.
    nullable = len(found) != len(publications) or any(field.nullable for field in found)
    authoritative = found[-1]
    return pa.field(
        column,
        authoritative.type,
        nullable=nullable,
        metadata=authoritative.metadata,
    )


def _select_artifacts(
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
            # Este filtro ya se resuelve por identidad de la parte y no llega a PyArrow.
            artifacts = tuple(
                artifact
                for artifact in artifacts
                if artifact.part_value is not None
                and _part_filter_matches(part_value=artifact.part_value, item=item)
            )
        else:
            residual.append(item)
    return artifacts, tuple(residual)


def _to_parquet_filter(*, item: ColumnFilter, field: pa.Field) -> tuple[str, str, object]:
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


def _part_filter_matches(*, part_value: str, item: ColumnFilter) -> bool:
    if item.operator is FilterOperator.EQUAL:
        return part_value == str(item.value)
    return part_value in {str(value) for value in item.value}


def _empty_table(schema: pa.Schema) -> pa.Table:
    return pa.Table.from_arrays(
        [pa.array([], type=field.type) for field in schema],
        schema=schema,
    )
