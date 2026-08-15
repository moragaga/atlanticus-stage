from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import pyarrow as pa

from ada.processes.pi_web_api.errors import PiWebApiMaterializationError
from ada.processes.pi_web_api.models import (
    PiAcquisitionResult,
    PiAcquisitionWindow,
    PiMaterializationResult,
    PiSample,
)
from atlanticus.datasets import (
    DatasetDefinition,
    DatasetKey,
    DatasetPublicationResult,
    DatasetTarget,
    MaterializationDefinition,
    SingleArtifactLayout,
)
from atlanticus.datasets.runtime import DatasetRuntime, DatasetRuntimeNotFoundError
from atlanticus.integrations.pi.contracts import (
    PiCatalog,
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
    PiWebApiSource,
)
from atlanticus.runtime import JobRuntimeContext

_MATERIALIZATION_ORDER = (
    PiMaterialization.LATEST,
    PiMaterialization.DAILY,
    PiMaterialization.MONTHLY,
)
_TIMESTAMP_FIELD = pa.field('timestamp_utc', pa.timestamp('us', tz='UTC'), nullable=False)


class PiWebApiMaterializer:
    def __init__(
        self,
        *,
        runtime: DatasetRuntime,
        catalog: PiCatalog,
    ) -> None:
        if not isinstance(runtime, DatasetRuntime):
            raise TypeError('runtime must be a DatasetRuntime')
        if not isinstance(catalog, PiCatalog):
            raise TypeError('catalog must be a PiCatalog')
        if not isinstance(catalog.source, PiWebApiSource):
            raise PiWebApiMaterializationError('catalog source must be PiWebApiSource')
        if catalog.source.interpolation_seconds is None:
            raise PiWebApiMaterializationError('catalog must define interpolation_seconds')
        self._runtime = runtime
        self._catalog = catalog
        self._interpolation_seconds = catalog.source.interpolation_seconds
        self._definitions = {
            mode: tuple(
                definition
                for definition in catalog.definitions
                if definition.is_active and definition.extraction_mode is mode
            )
            for mode in PiExtractionMode
        }
        self._datasets = {
            mode: _build_dataset_definition(mode=mode, definitions=definitions)
            for mode, definitions in self._definitions.items()
            if definitions
        }

    def dataset_for(self, mode: PiExtractionMode) -> DatasetDefinition | None:
        if not isinstance(mode, PiExtractionMode):
            raise TypeError('mode must be a PiExtractionMode')
        return self._datasets.get(mode)

    def publish(
        self,
        *,
        window: PiAcquisitionWindow,
        acquisition: PiAcquisitionResult,
        context: JobRuntimeContext,
    ) -> PiMaterializationResult:
        if not isinstance(window, PiAcquisitionWindow):
            raise TypeError('window must be a PiAcquisitionWindow')
        if not isinstance(acquisition, PiAcquisitionResult):
            raise TypeError('acquisition must be a PiAcquisitionResult')
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be a JobRuntimeContext')
        if window.interpolation_seconds != self._interpolation_seconds:
            raise PiWebApiMaterializationError(
                'acquisition window interpolation does not match catalog source'
            )
        context.raise_if_cancelled()

        interpolated_values = _project_interpolated(
            samples=acquisition.interpolated,
            window=window,
        )
        recorded_values, recorded_second_conflicts = _project_recorded(
            samples=acquisition.recorded,
            window=window,
        )
        publications: list[DatasetPublicationResult] = []
        publications.extend(
            self._publish_mode(
                mode=PiExtractionMode.INTERPOLATED,
                window=window,
                values=interpolated_values,
                context=context,
            )
        )
        publications.extend(
            self._publish_mode(
                mode=PiExtractionMode.RECORDED,
                window=window,
                values=recorded_values,
                context=context,
            )
        )
        return PiMaterializationResult(
            publications=tuple(publications),
            recorded_second_conflict_count=recorded_second_conflicts,
        )

    def _publish_mode(
        self,
        *,
        mode: PiExtractionMode,
        window: PiAcquisitionWindow,
        values: dict[tuple[datetime, str], Any],
        context: JobRuntimeContext,
    ) -> tuple[DatasetPublicationResult, ...]:
        definitions = self._definitions.get(mode, ())
        dataset = self._datasets.get(mode)
        if not definitions or dataset is None:
            return ()
        publications: list[DatasetPublicationResult] = []
        for materialization in _MATERIALIZATION_ORDER:
            context.raise_if_cancelled()
            selected = tuple(
                definition
                for definition in definitions
                if materialization in definition.materializations
            )
            if not selected:
                continue
            timestamps = (
                _slots(window)
                if mode is PiExtractionMode.INTERPOLATED
                else _timestamps_for_definitions(values=values, definitions=selected)
            )
            if not timestamps:
                continue
            if materialization is PiMaterialization.LATEST:
                target = dataset.resolve_target(materialization=materialization.value)
                table = _build_table(
                    slots=(timestamps[-1],),
                    definitions=selected,
                    values=values,
                    schema=_current_schema(selected),
                )
                context.raise_if_cancelled()
                publications.append(
                    self._runtime.replace(definition=dataset, target=target, data=table)
                )
                continue
            grouped = _group_slots(timestamps, materialization)
            for partition, partition_slots in grouped:
                context.raise_if_cancelled()
                target = dataset.resolve_target(
                    materialization=materialization.value,
                    partition=partition,
                )
                if mode is PiExtractionMode.RECORDED:
                    existing = self._read_active_target(definition=dataset, target=target)
                    schema = _current_schema(selected)
                    if existing is not None:
                        schema = _merge_active_schema(existing=existing.schema, current=schema)
                else:
                    existing = None
                    schema = self._schema_for_active_target(
                        dataset=dataset,
                        target=target,
                        definitions=selected,
                    )
                table = _build_table(
                    slots=partition_slots,
                    definitions=selected,
                    values=values,
                    schema=schema,
                )
                if mode is PiExtractionMode.RECORDED and existing is not None:
                    table = _preserve_existing_on_null(existing=existing, incoming=table)
                context.raise_if_cancelled()
                publications.append(
                    self._runtime.merge(
                        definition=dataset,
                        target=target,
                        data=table,
                        key_columns=('timestamp_utc',),
                        order_by=('timestamp_utc',),
                    )
                )
        return tuple(publications)

    def _schema_for_active_target(
        self,
        *,
        dataset: DatasetDefinition,
        target: DatasetTarget,
        definitions: tuple[PiTagDefinition, ...],
    ) -> pa.Schema:
        current = _current_schema(definitions)
        try:
            existing = self._runtime.read_schema(definition=dataset, target=target)
        except DatasetRuntimeNotFoundError:
            return current
        return _merge_active_schema(existing=existing, current=current)

    def _read_active_target(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
    ) -> pa.Table | None:
        try:
            return self._runtime.read_table(definition=definition, target=target).table
        except DatasetRuntimeNotFoundError:
            return None


def _build_dataset_definition(
    *,
    mode: PiExtractionMode,
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
        key=DatasetKey(namespace=('pi', 'web-api'), name=mode.value),
        materializations=tuple(materializations),
    )


def _slots(window: PiAcquisitionWindow) -> tuple[datetime, ...]:
    return tuple(
        window.first_slot_utc + timedelta(seconds=index * window.interpolation_seconds)
        for index in range(window.slot_count)
    )


def _timestamps_for_definitions(
    *,
    values: dict[tuple[datetime, str], Any],
    definitions: tuple[PiTagDefinition, ...],
) -> tuple[datetime, ...]:
    selected = {definition.tag_name.casefold(): definition for definition in definitions}
    timestamps: set[datetime] = set()
    for (timestamp, tag_name), value in values.items():
        definition = selected.get(tag_name)
        if definition is None or _normalize_value(value, definition) is None:
            continue
        timestamps.add(timestamp)
    return tuple(sorted(timestamps))


def _group_slots(
    slots: tuple[datetime, ...],
    materialization: PiMaterialization,
) -> tuple[tuple[dict[str, str], tuple[datetime, ...]], ...]:
    grouped: dict[tuple[str, ...], list[datetime]] = defaultdict(list)
    for slot in slots:
        if materialization is PiMaterialization.DAILY:
            key = slot.strftime('%Y'), slot.strftime('%m'), slot.strftime('%d')
        elif materialization is PiMaterialization.MONTHLY:
            key = slot.strftime('%Y'), slot.strftime('%m')
        else:
            raise PiWebApiMaterializationError('unsupported partitioned materialization')
        grouped[key].append(slot)
    output: list[tuple[dict[str, str], tuple[datetime, ...]]] = []
    for key in sorted(grouped):
        if materialization is PiMaterialization.DAILY:
            partition = {'year': key[0], 'month': key[1], 'day': key[2]}
        else:
            partition = {'year': key[0], 'month': key[1]}
        output.append((partition, tuple(grouped[key])))
    return tuple(output)


def _project_interpolated(
    *,
    samples: tuple[PiSample, ...],
    window: PiAcquisitionWindow,
) -> dict[tuple[datetime, str], Any]:
    values: dict[tuple[datetime, str], Any] = {}
    for sample in samples:
        slot = _floor_slot(sample.timestamp_utc, window.interpolation_seconds)
        if window.first_slot_utc <= slot <= window.last_slot_utc:
            values[(slot, sample.tag_name.casefold())] = sample.value
    return values


def _project_recorded(
    *,
    samples: tuple[PiSample, ...],
    window: PiAcquisitionWindow,
) -> tuple[dict[tuple[datetime, str], Any], int]:
    selected: dict[tuple[datetime, str], PiSample] = {}
    conflicts = 0
    end_exclusive = window.last_slot_utc + timedelta(seconds=window.interpolation_seconds)
    for sample in samples:
        if not window.first_slot_utc <= sample.timestamp_utc < end_exclusive:
            continue
        timestamp = sample.timestamp_utc.replace(microsecond=0)
        key = (timestamp, sample.tag_name.casefold())
        previous = selected.get(key)
        if previous is not None:
            conflicts += 1
        if previous is None or sample.timestamp_utc >= previous.timestamp_utc:
            selected[key] = sample
    return {key: sample.value for key, sample in selected.items()}, conflicts


def _floor_slot(value: datetime, interpolation_seconds: int) -> datetime:
    normalized = value.astimezone(UTC)
    epoch_seconds = math.floor(normalized.timestamp())
    aligned = epoch_seconds - (epoch_seconds % interpolation_seconds)
    return datetime.fromtimestamp(aligned, tz=UTC)


def _current_schema(definitions: tuple[PiTagDefinition, ...]) -> pa.Schema:
    fields = [_TIMESTAMP_FIELD]
    fields.extend(_field_for_definition(definition) for definition in definitions)
    return pa.schema(fields)


def _merge_active_schema(*, existing: pa.Schema, current: pa.Schema) -> pa.Schema:
    if 'timestamp_utc' not in existing.names:
        raise PiWebApiMaterializationError('existing PI dataset schema has no timestamp_utc field')
    if existing.field('timestamp_utc').type != _TIMESTAMP_FIELD.type:
        raise PiWebApiMaterializationError('existing PI dataset timestamp_utc type is incompatible')
    current_by_name = {field.name: field for field in current}
    fields = [_TIMESTAMP_FIELD]
    seen = {'timestamp_utc'}
    for field in existing:
        if field.name == 'timestamp_utc':
            continue
        current_field = current_by_name.get(field.name)
        if current_field is not None and current_field.type != field.type:
            raise PiWebApiMaterializationError(
                f'existing PI dataset column type is incompatible: {field.name}'
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


def _build_table(
    *,
    slots: tuple[datetime, ...],
    definitions: tuple[PiTagDefinition, ...],
    values: dict[tuple[datetime, str], Any],
    schema: pa.Schema,
) -> pa.Table:
    definitions_by_alias = {definition.alias: definition for definition in definitions}
    arrays: list[pa.Array] = [pa.array(slots, type=_TIMESTAMP_FIELD.type)]
    for field in tuple(schema)[1:]:
        definition = definitions_by_alias.get(field.name)
        if definition is None:
            arrays.append(pa.nulls(len(slots), type=field.type))
            continue
        normalized = [
            _normalize_value(values.get((slot, definition.tag_name.casefold())), definition)
            for slot in slots
        ]
        arrays.append(pa.array(normalized, type=field.type, from_pandas=True))
    try:
        return pa.Table.from_arrays(arrays, schema=schema)
    except (pa.ArrowException, TypeError, ValueError) as error:
        raise PiWebApiMaterializationError(
            'PI values could not be converted to dataset schema'
        ) from error


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
    if value is None:
        return None
    if definition.value_kind is PiValueKind.NUMBER:
        if isinstance(value, bool):
            return None
        try:
            return float(value)
        except TypeError, ValueError, OverflowError:
            return None
    if isinstance(value, str):
        return value
    return str(value)
