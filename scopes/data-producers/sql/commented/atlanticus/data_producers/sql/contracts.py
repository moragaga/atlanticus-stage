# Define el contrato mínimo del ejecutor de una fuente SQL.
from __future__ import annotations

from typing import Protocol, runtime_checkable

from atlanticus.data_producers.sql.models import SqlSourceExecutionResult, SqlSourcePlan
from atlanticus.runtime import JobRuntimeContext


@runtime_checkable
class SqlSourceExecutor(Protocol):
    def execute(
        self,
        *,
        plan: SqlSourcePlan,
        context: JobRuntimeContext,
    ) -> SqlSourceExecutionResult: ...
