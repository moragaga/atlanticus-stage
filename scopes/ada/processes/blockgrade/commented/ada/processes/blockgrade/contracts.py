# Define el contrato mínimo que el job exige a un ejecutor de fuentes Blockgrade.
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ada.processes.blockgrade.models import BlockgradeSourceExecutionResult, BlockgradeSourcePlan
from atlanticus.runtime import JobRuntimeContext


@runtime_checkable
class BlockgradeSourceExecutor(Protocol):
    def execute(
        self,
        *,
        plan: BlockgradeSourcePlan,
        context: JobRuntimeContext,
    ) -> BlockgradeSourceExecutionResult: ...
