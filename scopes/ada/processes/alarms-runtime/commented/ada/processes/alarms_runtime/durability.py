from __future__ import annotations

# Esta clase mantiene la frontera de escritura durable bajo la autoridad de JobRuntimeContext.
# La composición superior entrega EngineCommitRecord ya construido; aquí sólo se ejecuta recovery/commit con fencing.
from collections.abc import Sequence
from dataclasses import dataclass

from ada.alarms.persistence import (
    AlarmPersistence,
    CommitBatchResult,
    EngineCommitRecord,
    RecoveryResult,
)
from atlanticus.runtime import JobRuntimeContext


@dataclass(slots=True)
class AlarmRuntimeDurability:
    persistence: AlarmPersistence

    def __post_init__(self) -> None:
        if not isinstance(self.persistence, AlarmPersistence):
            raise TypeError('persistence must be an AlarmPersistence')

    def recover(self, context: JobRuntimeContext) -> RecoveryResult:
        _require_context(context)
        context.raise_if_cancelled()
        result = self.persistence.recover(
            assert_authority=context.assert_lease_current,
            fenced_mutation=context.fenced_mutation,
        )
        context.set_execution_fact('alarm_recovery_applied_count', result.applied_count)
        context.set_execution_fact('alarm_recovery_skipped_count', result.skipped_count)
        context.set_execution_fact(
            'alarm_recovery_discarded_tail_bytes',
            result.discarded_tail_bytes,
        )
        context.set_execution_fact(
            'alarm_recovery_sealed_segment_count',
            result.sealed_segment_count,
        )
        return result

    def commit_batch(
        self,
        context: JobRuntimeContext,
        records: Sequence[EngineCommitRecord],
    ) -> CommitBatchResult:
        _require_context(context)
        context.raise_if_cancelled()
        result = self.persistence.commit_batch(
            records,
            assert_authority=context.assert_lease_current,
            fenced_mutation=context.fenced_mutation,
        )
        context.mark_iteration_work()
        context.set_iteration_fact('alarm_commit_record_count', result.record_count)
        context.set_iteration_fact('alarm_commit_bytes_appended', result.bytes_appended)
        context.set_iteration_fact('alarm_commit_sealed_segment_count', result.sealed_segment_count)
        context.increment_execution_counter('alarm_commits_confirmed', result.record_count)
        context.increment_execution_counter('alarm_commit_bytes_appended', result.bytes_appended)
        return result


def _require_context(value: JobRuntimeContext) -> None:
    if not isinstance(value, JobRuntimeContext):
        raise TypeError('context must be a JobRuntimeContext')
