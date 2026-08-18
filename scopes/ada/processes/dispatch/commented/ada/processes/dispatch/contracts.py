# Define el contrato mínimo que el job exige a un ejecutor de fuentes Dispatch.
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ada.processes.dispatch.models import DispatchSourceExecutionResult, DispatchSourcePlan
from atlanticus.runtime import JobRuntimeContext


@runtime_checkable
class DispatchSourceExecutor(Protocol):
    def execute(
        self,
        *,
        plan: DispatchSourcePlan,
        context: JobRuntimeContext,
    ) -> DispatchSourceExecutionResult: ...
