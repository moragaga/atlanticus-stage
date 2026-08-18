from __future__ import annotations

from ada.processes.dispatch.curation import curate_dispatch_table, source_last_update_utc
from ada.processes.dispatch.extraction import DispatchSqlReader
from ada.processes.dispatch.materialization import DispatchMaterializer
from ada.processes.dispatch.models import DispatchSourceExecutionResult, DispatchSourcePlan
from atlanticus.runtime import JobRuntimeContext


class DispatchSourceProcessor:
    def __init__(
        self,
        *,
        reader: DispatchSqlReader,
        materializer: DispatchMaterializer,
    ) -> None:
        if not isinstance(reader, DispatchSqlReader):
            raise TypeError('reader must be a DispatchSqlReader')
        if not isinstance(materializer, DispatchMaterializer):
            raise TypeError('materializer must be a DispatchMaterializer')
        self._reader = reader
        self._materializer = materializer

    def execute(
        self,
        *,
        plan: DispatchSourcePlan,
        context: JobRuntimeContext,
    ) -> DispatchSourceExecutionResult:
        if not isinstance(plan, DispatchSourcePlan):
            raise TypeError('plan must be a DispatchSourcePlan')
        context.raise_if_cancelled()
        raw = self._reader.read_source(plan, context=context)
        context.raise_if_cancelled()
        curated = curate_dispatch_table(definition=plan.definition, table=raw)
        context.raise_if_cancelled()
        publications, missing_shift_ids = self._materializer.publish(
            plan=plan,
            table=curated,
            context=context,
        )
        context.raise_if_cancelled()
        return DispatchSourceExecutionResult(
            source_key=plan.definition.source_key,
            source_row_count=curated.num_rows,
            publications=publications,
            missing_shift_ids=missing_shift_ids,
            source_last_update_utc=source_last_update_utc(
                definition=plan.definition,
                table=curated,
            ),
        )
