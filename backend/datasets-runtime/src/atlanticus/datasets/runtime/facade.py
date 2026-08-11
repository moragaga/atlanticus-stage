"""Fachada bidireccional para publicar y consumir datasets operacionales."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from time import monotonic

from atlanticus.datasets import (
    DatasetDefinition,
    DatasetPartKey,
    DatasetPublicationResult,
    DatasetTarget,
    DatasetValidationError,
    FileSetLayout,
    SingleArtifactLayout,
)
from atlanticus.datasets.parquet import (
    ColumnFilter,
    ParquetDatasetStore,
    ParquetPart,
    ParquetPublicationNotFoundError,
    ParquetReadError,
    ParquetReadResult,
    ParquetWriteError,
)
from atlanticus.datasets.runtime.conversion import (
    TabularData,
    normalize_column_names,
    to_arrow_table,
    to_pandas_dataframe,
    validate_merge_table,
)
from atlanticus.datasets.runtime.errors import (
    DatasetRuntimeNotFoundError,
    DatasetRuntimeReadError,
    DatasetRuntimeValidationError,
    DatasetRuntimeWriteError,
)
from atlanticus.datasets.runtime.models import (
    DataFrameReadResult,
    RuntimeDatasetPart,
    TableReadResult,
)


class DatasetRuntime:
    """Convierte formatos y delega atomicidad, filtros y archivos al store inyectado."""

    def __init__(
        self,
        *,
        store: ParquetDatasetStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(store, ParquetDatasetStore):
            raise DatasetRuntimeValidationError('store must be a ParquetDatasetStore')
        if clock is not None and not callable(clock):
            raise DatasetRuntimeValidationError('clock must be callable or None')
        self._store = store
        self._clock = clock or _utc_now

    def replace(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        data: TabularData,
    ) -> DatasetPublicationResult:
        """Publica un artefacto completo desde Pandas o PyArrow."""

        started = monotonic()
        _validate_request(
            definition=definition,
            target=target,
            layout_type=SingleArtifactLayout,
            operation='replace',
        )
        table = to_arrow_table(data)
        if table.num_rows == 0:
            return self._skipped_empty(target=target, started=started)
        try:
            return self._store.replace(definition=definition, target=target, table=table)
        except DatasetValidationError as error:
            raise DatasetRuntimeValidationError('invalid dataset replacement request') from error
        except ParquetWriteError as error:
            raise DatasetRuntimeWriteError(
                f'could not replace dataset target {target.identifier}'
            ) from error
        except Exception as error:
            raise DatasetRuntimeWriteError(
                f'could not replace dataset target {target.identifier}'
            ) from error

    def merge(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        data: TabularData,
        key_columns: Iterable[str],
        order_by: Iterable[str] = (),
    ) -> DatasetPublicationResult:
        """Integra filas por claves luego de normalizar la entrada a Arrow."""

        started = monotonic()
        _validate_request(
            definition=definition,
            target=target,
            layout_type=SingleArtifactLayout,
            operation='merge',
        )
        table = to_arrow_table(data)
        keys = normalize_column_names(key_columns, field='key_columns', allow_empty=False)
        ordering = normalize_column_names(order_by, field='order_by', allow_empty=True)
        validate_merge_table(table, key_columns=keys, order_by=ordering)
        if table.num_rows == 0:
            return self._skipped_empty(target=target, started=started)
        try:
            return self._store.merge(
                definition=definition,
                target=target,
                incoming=table,
                key_columns=keys,
                order_by=ordering,
            )
        except DatasetValidationError as error:
            raise DatasetRuntimeValidationError('invalid dataset merge request') from error
        except ParquetWriteError as error:
            raise DatasetRuntimeWriteError(
                f'could not merge dataset target {target.identifier}'
            ) from error
        except Exception as error:
            raise DatasetRuntimeWriteError(
                f'could not merge dataset target {target.identifier}'
            ) from error

    def publish_parts(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        parts: Iterable[RuntimeDatasetPart] = (),
        remove_parts: Iterable[DatasetPartKey] = (),
    ) -> DatasetPublicationResult:
        """Convierte cada parte y confirma la composición mediante una sola publicación."""

        started = monotonic()
        _validate_request(
            definition=definition,
            target=target,
            layout_type=FileSetLayout,
            operation='publish_parts',
        )
        incoming = _normalize_parts(parts=parts, target=target)
        removals = _normalize_removals(remove_parts=remove_parts, target=target)
        overlap = {part.key for part in incoming} & set(removals)
        if overlap:
            identifiers = sorted(item.identifier for item in overlap)
            raise DatasetRuntimeValidationError(
                f'parts cannot be incoming and removed in the same publication: {identifiers}'
            )
        if not incoming and not removals:
            return self._skipped_empty(target=target, started=started)
        try:
            parquet_parts = tuple(
                ParquetPart(key=part.key, table=to_arrow_table(part.data, field='part data'))
                for part in incoming
            )
        except DatasetValidationError as error:
            raise DatasetRuntimeValidationError('invalid incoming dataset part') from error
        if any(part.table.num_rows == 0 for part in parquet_parts):
            return self._skipped_empty(target=target, started=started)
        try:
            return self._store.publish_parts(
                definition=definition,
                target=target,
                incoming_parts=parquet_parts,
                remove_parts=removals,
            )
        except DatasetValidationError as error:
            raise DatasetRuntimeValidationError(
                'invalid dataset parts publication request'
            ) from error
        except ParquetWriteError as error:
            raise DatasetRuntimeWriteError(
                f'could not publish dataset parts for {target.identifier}'
            ) from error
        except Exception as error:
            raise DatasetRuntimeWriteError(
                f'could not publish dataset parts for {target.identifier}'
            ) from error

    def read_table(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
    ) -> TableReadResult:
        """Lee el target vigente conservando su representación Arrow."""

        _validate_request(definition=definition, target=target)
        try:
            result = self._store.read(definition=definition, target=target)
        except ParquetPublicationNotFoundError as error:
            raise DatasetRuntimeNotFoundError(
                f'dataset target has no confirmed publication: {target.identifier}'
            ) from error
        except DatasetValidationError as error:
            raise DatasetRuntimeValidationError('invalid dataset read request') from error
        except ParquetReadError as error:
            raise DatasetRuntimeReadError(
                f'could not read dataset target {target.identifier}'
            ) from error
        except Exception as error:
            raise DatasetRuntimeReadError(
                f'could not read dataset target {target.identifier}'
            ) from error
        return _table_result(result)

    def read_dataframe(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
    ) -> DataFrameReadResult:
        """Lee el target vigente y entrega un DataFrame nuevo sin índice persistido."""

        return _dataframe_result(self.read_table(definition=definition, target=target))

    def scan_table(
        self,
        *,
        definition: DatasetDefinition,
        targets: Iterable[DatasetTarget],
        columns: Iterable[str] | None = None,
        filters: Iterable[ColumnFilter] = (),
    ) -> TableReadResult:
        """Proyecta y filtra targets explícitos conservando Arrow."""

        resolved_targets = _normalize_targets(targets)
        for target in resolved_targets:
            _validate_request(definition=definition, target=target)
        resolved_columns = (
            None
            if columns is None
            else normalize_column_names(columns, field='columns', allow_empty=False)
        )
        resolved_filters = _normalize_filters(filters)
        try:
            result = self._store.scan(
                definition=definition,
                targets=resolved_targets,
                columns=resolved_columns,
                filters=resolved_filters,
            )
        except ParquetPublicationNotFoundError as error:
            identifiers = ', '.join(target.identifier for target in resolved_targets)
            raise DatasetRuntimeNotFoundError(
                f'dataset targets include a target without a confirmed publication: {identifiers}'
            ) from error
        except DatasetValidationError as error:
            raise DatasetRuntimeValidationError('invalid dataset scan request') from error
        except ParquetReadError as error:
            identifiers = ', '.join(target.identifier for target in resolved_targets)
            raise DatasetRuntimeReadError(
                f'could not scan dataset targets: {identifiers}'
            ) from error
        except Exception as error:
            identifiers = ', '.join(target.identifier for target in resolved_targets)
            raise DatasetRuntimeReadError(
                f'could not scan dataset targets: {identifiers}'
            ) from error
        return _table_result(result)

    def scan_dataframe(
        self,
        *,
        definition: DatasetDefinition,
        targets: Iterable[DatasetTarget],
        columns: Iterable[str] | None = None,
        filters: Iterable[ColumnFilter] = (),
    ) -> DataFrameReadResult:
        """Proyecta y filtra targets explícitos y entrega un DataFrame nuevo."""

        return _dataframe_result(
            self.scan_table(
                definition=definition,
                targets=targets,
                columns=columns,
                filters=filters,
            )
        )

    def _skipped_empty(
        self,
        *,
        target: DatasetTarget,
        started: float,
    ) -> DatasetPublicationResult:
        return DatasetPublicationResult.skipped_empty(
            target=target,
            finished_at_utc=_resolve_clock(self._clock),
            duration_ms=(monotonic() - started) * 1_000,
        )


def _normalize_parts(
    *,
    parts: Iterable[RuntimeDatasetPart],
    target: DatasetTarget,
) -> tuple[RuntimeDatasetPart, ...]:
    if isinstance(parts, RuntimeDatasetPart | str | bytes):
        raise DatasetRuntimeValidationError(
            'parts must be an iterable of RuntimeDatasetPart values'
        )
    try:
        resolved = tuple(parts)
    except TypeError as error:
        raise DatasetRuntimeValidationError(
            'parts must be an iterable of RuntimeDatasetPart values'
        ) from error
    if not all(isinstance(item, RuntimeDatasetPart) for item in resolved):
        raise DatasetRuntimeValidationError('parts must contain only RuntimeDatasetPart values')
    if any(item.key.target != target for item in resolved):
        raise DatasetRuntimeValidationError('all parts must reference the requested target')
    keys = tuple(item.key for item in resolved)
    if len(set(keys)) != len(keys):
        raise DatasetRuntimeValidationError('parts must not contain duplicate keys')
    return resolved


def _normalize_removals(
    *,
    remove_parts: Iterable[DatasetPartKey],
    target: DatasetTarget,
) -> tuple[DatasetPartKey, ...]:
    if isinstance(remove_parts, DatasetPartKey | str | bytes):
        raise DatasetRuntimeValidationError(
            'remove_parts must be an iterable of DatasetPartKey values'
        )
    try:
        resolved = tuple(remove_parts)
    except TypeError as error:
        raise DatasetRuntimeValidationError(
            'remove_parts must be an iterable of DatasetPartKey values'
        ) from error
    if not all(isinstance(item, DatasetPartKey) for item in resolved):
        raise DatasetRuntimeValidationError('remove_parts must contain only DatasetPartKey values')
    if any(item.target != target for item in resolved):
        raise DatasetRuntimeValidationError('all removed parts must reference the requested target')
    if len(set(resolved)) != len(resolved):
        raise DatasetRuntimeValidationError('remove_parts must not contain duplicates')
    return resolved


def _normalize_targets(targets: Iterable[DatasetTarget]) -> tuple[DatasetTarget, ...]:
    if isinstance(targets, DatasetTarget | str | bytes):
        raise DatasetRuntimeValidationError('targets must be an iterable of DatasetTarget values')
    try:
        resolved = tuple(targets)
    except TypeError as error:
        raise DatasetRuntimeValidationError(
            'targets must be an iterable of DatasetTarget values'
        ) from error
    if not resolved or not all(isinstance(item, DatasetTarget) for item in resolved):
        raise DatasetRuntimeValidationError('targets must contain DatasetTarget values')
    if len(set(resolved)) != len(resolved):
        raise DatasetRuntimeValidationError('targets must not contain duplicates')
    return resolved


def _normalize_filters(filters: Iterable[ColumnFilter]) -> tuple[ColumnFilter, ...]:
    if isinstance(filters, ColumnFilter | str | bytes):
        raise DatasetRuntimeValidationError('filters must be an iterable of ColumnFilter values')
    try:
        resolved = tuple(filters)
    except TypeError as error:
        raise DatasetRuntimeValidationError(
            'filters must be an iterable of ColumnFilter values'
        ) from error
    if not all(isinstance(item, ColumnFilter) for item in resolved):
        raise DatasetRuntimeValidationError('filters must contain only ColumnFilter values')
    return resolved


def _validate_request(
    *,
    definition: DatasetDefinition,
    target: DatasetTarget,
    layout_type: type[SingleArtifactLayout] | type[FileSetLayout] | None = None,
    operation: str | None = None,
) -> None:
    if not isinstance(definition, DatasetDefinition):
        raise DatasetRuntimeValidationError('definition must be a DatasetDefinition')
    try:
        definition.validate_target(target)
        materialization = definition.get_materialization(target.materialization)
    except DatasetValidationError as error:
        raise DatasetRuntimeValidationError('target does not belong to definition') from error
    if layout_type is not None and not isinstance(materialization.layout, layout_type):
        raise DatasetRuntimeValidationError(
            f'{operation} is not supported for {type(materialization.layout).__name__}'
        )


def _table_result(result: ParquetReadResult) -> TableReadResult:
    if not isinstance(result, ParquetReadResult):
        raise DatasetRuntimeReadError('store read did not return a ParquetReadResult')
    return TableReadResult(
        table=result.table,
        targets=result.targets,
        artifact_count=result.artifact_count,
        size_bytes=result.size_bytes,
        publication_tokens=result.publication_tokens,
        warnings=result.warnings,
    )


def _dataframe_result(result: TableReadResult) -> DataFrameReadResult:
    return DataFrameReadResult(
        dataframe=to_pandas_dataframe(result.table),
        targets=result.targets,
        artifact_count=result.artifact_count,
        size_bytes=result.size_bytes,
        publication_tokens=result.publication_tokens,
        warnings=result.warnings,
    )


def _resolve_clock(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DatasetRuntimeValidationError('clock must return a timezone-aware datetime')
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)
