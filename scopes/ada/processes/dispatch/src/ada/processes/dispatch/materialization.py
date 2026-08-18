from __future__ import annotations

import pyarrow as pa
import pyarrow.compute as pc

from ada.operational_calendar import parse_shift_id_turn
from ada.processes.dispatch.errors import DispatchMaterializationError, DispatchSchemaError
from ada.processes.dispatch.models import (
    DispatchLoadStrategy,
    DispatchPublicationResult,
    DispatchSourceDefinition,
    DispatchSourcePlan,
    DispatchStorageMode,
)
from atlanticus.datasets.layouts import SingleArtifactLayout
from atlanticus.datasets.models import DatasetDefinition, DatasetKey, MaterializationDefinition
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.runtime import JobRuntimeContext

_SHIFT_MATERIALIZATION = 'shift'
_LATEST_MATERIALIZATION = 'latest'
_SHIFT_PARTITION_DIMENSIONS = ('year', 'month', 'day', 'turn')


class DispatchMaterializer:
    def __init__(
        self,
        *,
        runtime: DatasetRuntime,
        definitions: tuple[DispatchSourceDefinition, ...],
    ) -> None:
        if not isinstance(runtime, DatasetRuntime):
            raise TypeError('runtime must be a DatasetRuntime')
        if not definitions or not all(
            isinstance(definition, DispatchSourceDefinition) for definition in definitions
        ):
            raise TypeError('definitions must contain DispatchSourceDefinition values')
        self._runtime = runtime
        self._datasets = {
            definition.source_key: _build_dataset_definition(definition)
            for definition in definitions
        }

    def publish(
        self,
        *,
        plan: DispatchSourcePlan,
        table: pa.Table,
        context: JobRuntimeContext | None = None,
    ) -> tuple[tuple[DispatchPublicationResult, ...], tuple[int, ...]]:
        if not isinstance(plan, DispatchSourcePlan):
            raise TypeError('plan must be a DispatchSourcePlan')
        if not isinstance(table, pa.Table):
            raise TypeError('table must be a pyarrow.Table')
        if plan.definition.source_key not in self._datasets:
            raise DispatchMaterializationError('source is not part of this materializer')
        if context is not None:
            context.raise_if_cancelled()
        if plan.definition.load_strategy is DispatchLoadStrategy.SHIFT_WINDOW:
            return self._publish_shift_window(plan=plan, table=table, context=context)
        return self._publish_latest(plan=plan, table=table, context=context), ()

    def _publish_shift_window(
        self,
        *,
        plan: DispatchSourcePlan,
        table: pa.Table,
        context: JobRuntimeContext | None = None,
    ) -> tuple[tuple[DispatchPublicationResult, ...], tuple[int, ...]]:
        definition = plan.definition
        shift_column = definition.shift_id_output_column
        if shift_column not in table.column_names:
            raise DispatchSchemaError('Dispatch shift column is missing from curated data')
        values = table[shift_column].combine_chunks().to_pylist()
        if any(value is None for value in values):
            raise DispatchSchemaError('Dispatch shift identifier must not contain null values')
        try:
            source_shift_ids = {int(value) for value in values}
        except (TypeError, ValueError) as error:
            raise DispatchSchemaError('Dispatch shift identifier is invalid') from error
        expected_shift_ids = set(plan.shift_ids)
        if not source_shift_ids.issubset(expected_shift_ids):
            raise DispatchSchemaError('Dispatch source returned an unexpected shift identifier')
        dataset = self._datasets[definition.source_key]
        publications: list[DispatchPublicationResult] = []
        for shift_id in plan.shift_ids:
            if context is not None:
                context.raise_if_cancelled()
            if shift_id not in source_shift_ids:
                continue
            turn = parse_shift_id_turn(shift_id)
            mask = pc.equal(table[shift_column], pa.scalar(shift_id, type=pa.int64()))
            shift_table = table.filter(mask)
            target = dataset.resolve_target(
                materialization=_SHIFT_MATERIALIZATION,
                partition=turn.partition,
            )
            publication = self._runtime.replace(
                definition=dataset,
                target=target,
                data=shift_table,
            )
            publications.append(
                DispatchPublicationResult(publication=publication, shift_id=shift_id)
            )
        missing = tuple(shift_id for shift_id in plan.shift_ids if shift_id not in source_shift_ids)
        return tuple(publications), missing

    def _publish_latest(
        self,
        *,
        plan: DispatchSourcePlan,
        table: pa.Table,
        context: JobRuntimeContext | None = None,
    ) -> tuple[DispatchPublicationResult, ...]:
        if context is not None:
            context.raise_if_cancelled()
        dataset = self._datasets[plan.definition.source_key]
        target = dataset.resolve_target(materialization=_LATEST_MATERIALIZATION)
        publication = self._runtime.replace(
            definition=dataset,
            target=target,
            data=table,
        )
        return (DispatchPublicationResult(publication=publication),)


def _build_dataset_definition(definition: DispatchSourceDefinition) -> DatasetDefinition:
    if definition.storage_mode is DispatchStorageMode.SHIFT:
        materialization = MaterializationDefinition(
            name=_SHIFT_MATERIALIZATION,
            layout=SingleArtifactLayout(),
            partition_dimensions=_SHIFT_PARTITION_DIMENSIONS,
        )
    else:
        materialization = MaterializationDefinition(
            name=_LATEST_MATERIALIZATION,
            layout=SingleArtifactLayout(),
        )
    return DatasetDefinition(
        key=DatasetKey(namespace=('dispatch',), name=definition.source_key),
        materializations=(materialization,),
    )
