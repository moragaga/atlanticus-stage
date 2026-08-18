# Espejo comentado del proceso NOTPII: composición, batch, materialización, estado y settlement.
# Este archivo conserva exactamente el comportamiento productivo y agrega solo contexto.
from __future__ import annotations

from typing import Any

import pandas as pd
import pyarrow as pa

from ada.connectors.notpii import NotPiiBatch
from ada.processes.notpii.errors import NotPiiMaterializationError, NotPiiProcessConfigurationError
from atlanticus.datasets.layouts import SingleArtifactLayout
from atlanticus.datasets.models import (
    DatasetDefinition,
    DatasetKey,
    DatasetTarget,
    MaterializationDefinition,
)
from atlanticus.datasets.results import DatasetPublicationResult
from atlanticus.datasets.runtime import DatasetRuntime, DatasetRuntimeNotFoundError
from atlanticus.integrations.pi.contracts import (
    NotPiiSource,
    PiCatalog,
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
)

_MATERIALIZATION_ORDER = (
    PiMaterialization.LATEST,
    PiMaterialization.DAILY,
    PiMaterialization.MONTHLY,
)
_TIMESTAMP_FIELD = pa.field('timestamp_utc', pa.timestamp('us', tz='UTC'), nullable=False)


class NotPiiMaterializer:
    def __init__(
        self,
        *,
        runtime: DatasetRuntime,
        catalog: PiCatalog,
        extraction_mode: PiExtractionMode,
    ) -> None:
        if not isinstance(runtime, DatasetRuntime):
            raise NotPiiProcessConfigurationError('runtime must be a DatasetRuntime')
        if not isinstance(catalog, PiCatalog):
            raise NotPiiProcessConfigurationError('catalog must be a PiCatalog')
        if not isinstance(catalog.source, NotPiiSource):
            raise NotPiiProcessConfigurationError('catalog source must be NotPiiSource')
        if not isinstance(extraction_mode, PiExtractionMode):
            raise NotPiiProcessConfigurationError('extraction_mode must be a PiExtractionMode')
        definitions = tuple(
            item
            for item in catalog.definitions
            if item.is_active and item.extraction_mode is extraction_mode
        )
        if not definitions:
            raise NotPiiProcessConfigurationError(
                'NOT PII catalog does not contain the configured extraction mode'
            )
        self._runtime = runtime
        self._extraction_mode = extraction_mode
        self._definitions = definitions
        self._dataset = _build_dataset_definition(
            extraction_mode=extraction_mode,
            definitions=definitions,
        )

    @property
    def dataset(self) -> DatasetDefinition:
        return self._dataset

    def publish(self, batch: NotPiiBatch) -> tuple[DatasetPublicationResult, ...]:
        if not isinstance(batch, NotPiiBatch):
            raise NotPiiMaterializationError('batch must be a NotPiiBatch')
        if batch.extraction_mode is not self._extraction_mode:
            raise NotPiiMaterializationError(
                'batch extraction mode does not match the materializer'
            )
        frame = _normalize_frame(batch.data)
        if frame.empty:
            return ()

        publications: list[DatasetPublicationResult] = []
        for materialization in _MATERIALIZATION_ORDER:
            definitions = tuple(
                item
                for item in self._definitions
                if materialization in item.materializations
            )
            if not definitions:
                continue
            if materialization is PiMaterialization.LATEST:
                target = self._dataset.resolve_target(materialization=materialization.value)
                latest = frame.sort_values('timestamp_utc', kind='stable').tail(1)
                publications.append(
                    self._runtime.replace(
                        definition=self._dataset,
                        target=target,
                        data=_project_table(
                            frame=latest,
                            definitions=definitions,
                            schema=_current_schema(definitions),
                        ),
                    )
                )
                continue
            for target, group in self._partition_groups(
                frame=frame,
                materialization=materialization,
            ):
                publications.append(
                    self._merge(
                        target=target,
                        frame=group,
                        definitions=definitions,
                    )
                )
        return tuple(publications)

    def _partition_groups(
        self,
        *,
        frame: pd.DataFrame,
        materialization: PiMaterialization,
    ) -> tuple[tuple[DatasetTarget, pd.DataFrame], ...]:
        dimensions = ['_year', '_month']
        working = frame.assign(
            _year=frame['timestamp_utc'].dt.strftime('%Y'),
            _month=frame['timestamp_utc'].dt.strftime('%m'),
        )
        if materialization is PiMaterialization.DAILY:
            working = working.assign(_day=frame['timestamp_utc'].dt.strftime('%d'))
            dimensions.append('_day')
        groups: list[tuple[DatasetTarget, pd.DataFrame]] = []
        for partition, group in working.groupby(dimensions, sort=True, observed=True):
            values = (partition,) if isinstance(partition, str) else partition
            keys = ('year', 'month', 'day') if len(values) == 3 else ('year', 'month')
            groups.append(
                (
                    self._dataset.resolve_target(
                        materialization=materialization.value,
                        partition=dict(zip(keys, values, strict=True)),
                    ),
                    group,
                )
            )
        return tuple(groups)

    def _merge(
        self,
        *,
        target: DatasetTarget,
        frame: pd.DataFrame,
        definitions: tuple[PiTagDefinition, ...],
    ) -> DatasetPublicationResult:
        current = _current_schema(definitions)
        existing = self._read_active_target(target)
        schema = current if existing is None else _merge_active_schema(
            existing=existing.schema,
            current=current,
        )
        incoming = _project_table(frame=frame, definitions=definitions, schema=schema)
        if existing is not None:
            incoming = _preserve_existing_on_null(existing=existing, incoming=incoming)
        return self._runtime.merge(
            definition=self._dataset,
            target=target,
            data=incoming,
            key_columns=('timestamp_utc',),
            order_by=('timestamp_utc',),
        )

    def _read_active_target(self, target: DatasetTarget) -> pa.Table | None:
        try:
            return self._runtime.read_table(definition=self._dataset, target=target).table
        except DatasetRuntimeNotFoundError:
            return None


def _build_dataset_definition(
    *,
    extraction_mode: PiExtractionMode,
    definitions: tuple[PiTagDefinition, ...],
) -> DatasetDefinition:
    enabled = {
        materialization
        for definition in definitions
        for materialization in definition.materializations
    }
    materializations: list[MaterializationDefinition] = []
    for materialization in _MATERIALIZATION_ORDER:
        if materialization not in enabled:
            continue
        dimensions: tuple[str, ...] = ()
        if materialization is PiMaterialization.DAILY:
            dimensions = ('year', 'month', 'day')
        elif materialization is PiMaterialization.MONTHLY:
            dimensions = ('year', 'month')
        materializations.append(
            MaterializationDefinition(
                name=materialization.value,
                layout=SingleArtifactLayout(),
                partition_dimensions=dimensions,
            )
        )
    return DatasetDefinition(
        key=DatasetKey(namespace=('pi', 'not_pii'), name=extraction_mode.value),
        materializations=tuple(materializations),
    )


def _normalize_frame(data: pd.DataFrame) -> pd.DataFrame:
    frame = data.copy()
    timestamps = pd.to_datetime(frame['timestamp_utc'], utc=True, errors='coerce')
    if timestamps.isna().any():
        raise NotPiiMaterializationError('NOT PII batch contains invalid timestamp_utc values')
    frame['timestamp_utc'] = timestamps
    return frame.sort_values('timestamp_utc', kind='stable').reset_index(drop=True)


def _current_schema(definitions: tuple[PiTagDefinition, ...]) -> pa.Schema:
    return pa.schema([_TIMESTAMP_FIELD, *(_field_for_definition(item) for item in definitions)])


def _merge_active_schema(*, existing: pa.Schema, current: pa.Schema) -> pa.Schema:
    if 'timestamp_utc' not in existing.names:
        raise NotPiiMaterializationError('existing NOT PII dataset has no timestamp_utc field')
    if existing.field('timestamp_utc').type != _TIMESTAMP_FIELD.type:
        raise NotPiiMaterializationError('existing NOT PII timestamp_utc type is incompatible')
    current_by_name = {field.name: field for field in current}
    fields = [_TIMESTAMP_FIELD]
    seen = {'timestamp_utc'}
    for field in existing:
        if field.name == 'timestamp_utc':
            continue
        current_field = current_by_name.get(field.name)
        if current_field is not None and current_field.type != field.type:
            raise NotPiiMaterializationError(
                f'existing NOT PII dataset column type is incompatible: {field.name}'
            )
        fields.append(current_field or field)
        seen.add(field.name)
    for field in current:
        if field.name not in seen:
            fields.append(field)
            seen.add(field.name)
    return pa.schema(fields)


def _field_for_definition(definition: PiTagDefinition) -> pa.Field:
    if definition.value_kind is PiValueKind.NUMBER:
        return pa.field(definition.alias, pa.float64(), nullable=True)
    return pa.field(definition.alias, pa.string(), nullable=True)


def _project_table(
    *,
    frame: pd.DataFrame,
    definitions: tuple[PiTagDefinition, ...],
    schema: pa.Schema,
) -> pa.Table:
    definitions_by_alias = {item.alias: item for item in definitions}
    arrays: list[pa.Array] = [
        pa.array(frame['timestamp_utc'], type=_TIMESTAMP_FIELD.type, from_pandas=True)
    ]
    for field in tuple(schema)[1:]:
        definition = definitions_by_alias.get(field.name)
        if definition is None:
            arrays.append(pa.nulls(len(frame), type=field.type))
            continue
        values = (
            frame[definition.alias]
            if definition.alias in frame.columns
            else pd.Series(pd.NA, index=frame.index, dtype='object')
        )
        arrays.append(
            pa.array(
                [_normalize_value(value, definition) for value in values],
                type=field.type,
                from_pandas=True,
            )
        )
    try:
        return pa.Table.from_arrays(arrays, schema=schema)
    except (pa.ArrowException, TypeError, ValueError) as error:
        raise NotPiiMaterializationError('NOT PII values could not be converted') from error


def _preserve_existing_on_null(*, existing: pa.Table, incoming: pa.Table) -> pa.Table:
    if incoming.num_rows == 0 or existing.num_rows == 0:
        return incoming
    timestamps = incoming['timestamp_utc'].to_pylist()
    existing_timestamps = existing['timestamp_utc'].to_pylist()
    existing_index = {timestamp: index for index, timestamp in enumerate(existing_timestamps)}
    arrays: list[pa.Array] = [incoming['timestamp_utc'].combine_chunks()]
    for field in tuple(incoming.schema)[1:]:
        incoming_values = incoming[field.name].to_pylist()
        if field.name in existing.column_names:
            existing_values = existing[field.name].to_pylist()
            for index, timestamp in enumerate(timestamps):
                existing_row = existing_index.get(timestamp)
                if (
                    incoming_values[index] is None
                    and existing_row is not None
                    and existing_values[existing_row] is not None
                ):
                    incoming_values[index] = existing_values[existing_row]
        arrays.append(pa.array(incoming_values, type=field.type, from_pandas=True))
    return pa.Table.from_arrays(arrays, schema=incoming.schema)


def _normalize_value(value: Any, definition: PiTagDefinition) -> float | str | None:
    if value is None or pd.isna(value):
        return None
    if definition.value_kind is PiValueKind.NUMBER:
        if isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if isinstance(value, str):
        return value
    return str(value)
