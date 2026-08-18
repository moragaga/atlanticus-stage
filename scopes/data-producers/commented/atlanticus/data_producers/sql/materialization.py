# Publica datasets latest o particionados usando las particiones ya resueltas en SourceScope.
from __future__ import annotations

from collections.abc import Sequence

import pyarrow as pa
import pyarrow.compute as pc

from atlanticus.data_producers.sql.errors import (
    SqlDataProducerMaterializationError,
    SqlDataProducerSchemaError,
)
from atlanticus.data_producers.sql.models import (
    SqlLoadStrategy,
    SqlPublicationResult,
    SqlSourceDefinition,
    SqlSourcePlan,
    SqlStorageMode,
)
from atlanticus.datasets.layouts import SingleArtifactLayout
from atlanticus.datasets.models import DatasetDefinition, DatasetKey, MaterializationDefinition
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.runtime import JobRuntimeContext


class SqlDataProducerMaterializer:
    def __init__(
        self,
        *,
        runtime: DatasetRuntime,
        definitions: tuple[SqlSourceDefinition, ...],
        dataset_namespace: Sequence[str],
    ) -> None:
        if not isinstance(runtime, DatasetRuntime):
            raise TypeError('runtime must be a DatasetRuntime')
        if not definitions or not all(
            isinstance(definition, SqlSourceDefinition) for definition in definitions
        ):
            raise TypeError('definitions must contain SqlSourceDefinition values')
        namespace = tuple(_required_text(item, 'dataset namespace') for item in dataset_namespace)
        if not namespace:
            raise ValueError('dataset_namespace must contain at least one value')
        self._runtime = runtime
        self._datasets = {
            definition.source_key: _build_dataset_definition(definition, namespace=namespace)
            for definition in definitions
        }

    def publish(
        self,
        *,
        plan: SqlSourcePlan,
        table: pa.Table,
        context: JobRuntimeContext | None = None,
    ) -> tuple[tuple[SqlPublicationResult, ...], tuple[int | str, ...]]:
        if not isinstance(plan, SqlSourcePlan):
            raise TypeError('plan must be a SqlSourcePlan')
        if not isinstance(table, pa.Table):
            raise TypeError('table must be a pyarrow.Table')
        if plan.definition.source_key not in self._datasets:
            raise SqlDataProducerMaterializationError('source is not part of this materializer')
        if context is not None:
            context.raise_if_cancelled()
        if plan.definition.load_strategy is SqlLoadStrategy.SCOPED:
            return self._publish_scoped(plan=plan, table=table, context=context)
        return self._publish_latest(plan=plan, table=table, context=context), ()

    def _publish_scoped(
        self,
        *,
        plan: SqlSourcePlan,
        table: pa.Table,
        context: JobRuntimeContext | None = None,
    ) -> tuple[tuple[SqlPublicationResult, ...], tuple[int | str, ...]]:
        definition = plan.definition
        scope_column = definition.scope_output_column
        if scope_column is None or plan.scope is None:
            raise SqlDataProducerMaterializationError('scoped source has no scope contract')
        if scope_column not in table.column_names:
            raise SqlDataProducerSchemaError('scope output column is missing from curated data')
        values = table[scope_column].combine_chunks().to_pylist()
        if any(value is None for value in values):
            raise SqlDataProducerSchemaError('scope output column must not contain null values')
        source_values = set(values)
        expected_values = set(plan.scope.values)
        if not source_values.issubset(expected_values):
            raise SqlDataProducerSchemaError('source returned an unexpected scope value')
        dataset = self._datasets[definition.source_key]
        publications: list[SqlPublicationResult] = []
        column_type = table[scope_column].type
        for item in plan.scope.items:
            if context is not None:
                context.raise_if_cancelled()
            if item.value not in source_values:
                continue
            try:
                scalar = pa.scalar(item.value, type=column_type)
            except (pa.ArrowException, TypeError, ValueError) as error:
                raise SqlDataProducerSchemaError(
                    'scope value is incompatible with the curated scope column'
                ) from error
            mask = pc.equal(table[scope_column], scalar)
            scoped_table = table.filter(mask)
            target = dataset.resolve_target(
                materialization=definition.materialization_name,
                partition=dict(item.partition),
            )
            publication = self._runtime.replace(
                definition=dataset,
                target=target,
                data=scoped_table,
            )
            publications.append(
                SqlPublicationResult(publication=publication, scope_value=item.value)
            )
        missing = tuple(item.value for item in plan.scope.items if item.value not in source_values)
        return tuple(publications), missing

    def _publish_latest(
        self,
        *,
        plan: SqlSourcePlan,
        table: pa.Table,
        context: JobRuntimeContext | None = None,
    ) -> tuple[SqlPublicationResult, ...]:
        if context is not None:
            context.raise_if_cancelled()
        dataset = self._datasets[plan.definition.source_key]
        target = dataset.resolve_target(materialization=plan.definition.materialization_name)
        publication = self._runtime.replace(
            definition=dataset,
            target=target,
            data=table,
        )
        return (SqlPublicationResult(publication=publication),)


def _build_dataset_definition(
    definition: SqlSourceDefinition,
    *,
    namespace: tuple[str, ...],
) -> DatasetDefinition:
    if definition.storage_mode is SqlStorageMode.PARTITIONED:
        materialization = MaterializationDefinition(
            name=definition.materialization_name,
            layout=SingleArtifactLayout(),
            partition_dimensions=definition.partition_dimensions,
        )
    else:
        materialization = MaterializationDefinition(
            name=definition.materialization_name,
            layout=SingleArtifactLayout(),
        )
    return DatasetDefinition(
        key=DatasetKey(namespace=namespace, name=definition.source_key),
        materializations=(materialization,),
    )


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value.strip()
