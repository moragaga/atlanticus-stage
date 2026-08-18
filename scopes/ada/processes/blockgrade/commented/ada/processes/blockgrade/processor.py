# Orquesta una fuente: extracción SQL, curación Arrow y materialización, respetando cancelación.
from __future__ import annotations

from ada.processes.blockgrade.curation import curate_blockgrade_table, source_last_update_utc
from ada.processes.blockgrade.extraction import BlockgradeSqlReader
from ada.processes.blockgrade.materialization import BlockgradeMaterializer
from ada.processes.blockgrade.models import BlockgradeSourceExecutionResult, BlockgradeSourcePlan
from atlanticus.runtime import JobRuntimeContext


class BlockgradeSourceProcessor:
    def __init__(
        self,
        *,
        reader: BlockgradeSqlReader,
        materializer: BlockgradeMaterializer,
    ) -> None:
        if not isinstance(reader, BlockgradeSqlReader):
            raise TypeError('reader must be a BlockgradeSqlReader')
        if not isinstance(materializer, BlockgradeMaterializer):
            raise TypeError('materializer must be a BlockgradeMaterializer')
        self._reader = reader
        self._materializer = materializer

    def execute(
        self,
        *,
        plan: BlockgradeSourcePlan,
        context: JobRuntimeContext,
    ) -> BlockgradeSourceExecutionResult:
        if not isinstance(plan, BlockgradeSourcePlan):
            raise TypeError('plan must be a BlockgradeSourcePlan')
        context.raise_if_cancelled()
        raw = self._reader.read_source(plan, context=context)
        context.raise_if_cancelled()
        curated = curate_blockgrade_table(definition=plan.definition, table=raw)
        context.raise_if_cancelled()
        publications, missing_shift_ids = self._materializer.publish(
            plan=plan,
            table=curated,
            context=context,
        )
        context.raise_if_cancelled()
        return BlockgradeSourceExecutionResult(
            source_key=plan.definition.source_key,
            source_row_count=curated.num_rows,
            publications=publications,
            missing_shift_ids=missing_shift_ids,
            source_last_update_utc=source_last_update_utc(
                definition=plan.definition,
                table=curated,
            ),
        )
