# Coordina lectura, curación y materialización de una fuente SQL.
from __future__ import annotations

from atlanticus.data_producers.sql.curation import curate_table, source_last_update_utc
from atlanticus.data_producers.sql.extraction import SqlDataProducerReader
from atlanticus.data_producers.sql.materialization import SqlDataProducerMaterializer
from atlanticus.data_producers.sql.models import SqlSourceExecutionResult, SqlSourcePlan
from atlanticus.runtime import JobRuntimeContext


class SqlDataProducerProcessor:
    def __init__(
        self,
        *,
        reader: SqlDataProducerReader,
        materializer: SqlDataProducerMaterializer,
    ) -> None:
        if not isinstance(reader, SqlDataProducerReader):
            raise TypeError('reader must be a SqlDataProducerReader')
        if not isinstance(materializer, SqlDataProducerMaterializer):
            raise TypeError('materializer must be a SqlDataProducerMaterializer')
        self._reader = reader
        self._materializer = materializer

    def execute(
        self,
        *,
        plan: SqlSourcePlan,
        context: JobRuntimeContext,
    ) -> SqlSourceExecutionResult:
        if not isinstance(plan, SqlSourcePlan):
            raise TypeError('plan must be a SqlSourcePlan')
        context.raise_if_cancelled()
        raw = self._reader.read_source(plan, context=context)
        context.raise_if_cancelled()
        curated = curate_table(definition=plan.definition, table=raw)
        context.raise_if_cancelled()
        publications, missing_scope_values = self._materializer.publish(
            plan=plan,
            table=curated,
            context=context,
        )
        context.raise_if_cancelled()
        return SqlSourceExecutionResult(
            source_key=plan.definition.source_key,
            source_row_count=curated.num_rows,
            publications=publications,
            missing_scope_values=missing_scope_values,
            source_last_update_utc=source_last_update_utc(
                definition=plan.definition,
                table=curated,
            ),
        )
