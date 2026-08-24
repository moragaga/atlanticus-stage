from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ada.alarms.core import GroupCommitMaterialization, GroupLifecycleState, PlannedAlarm
from ada.alarms.persistence import (
    AlarmPersistence,
    CommitBatchResult,
    GroupRuntimeSnapshot,
    RecoveryResult,
)
from ada.processes.alarms_runtime.commit import compose_engine_commit_record
from ada.processes.alarms_runtime.durability import AlarmRuntimeDurability
from ada.processes.alarms_runtime.snapshot import (
    AlarmRuntimeCompositionError,
    decode_group_runtime_snapshot,
)
from atlanticus.runtime import JobRuntimeContext, RuntimeConfiguration


@dataclass(frozen=True, slots=True)
class AlarmRuntimeGroup:
    state: GroupLifecycleState
    snapshot: GroupRuntimeSnapshot | None

    @property
    def last_commit_id(self) -> str | None:
        return None if self.snapshot is None else self.snapshot.last_commit_id


@dataclass(slots=True)
class AlarmRuntimeComposition:
    runtime_configuration: RuntimeConfiguration
    durability: AlarmRuntimeDurability

    def recover(self, context: JobRuntimeContext) -> RecoveryResult:
        return self.durability.recover(context)

    def load_group(
        self,
        priority_group: str,
        *,
        planned_alarms: Sequence[PlannedAlarm],
    ) -> AlarmRuntimeGroup:
        if not isinstance(priority_group, str):
            raise TypeError('priority_group must be a string')
        if not priority_group.strip():
            raise ValueError('priority_group must not be empty')
        snapshot = self.durability.persistence.read_snapshot(priority_group)
        if snapshot is None:
            if self._has_durable_group_history(priority_group):
                raise AlarmRuntimeCompositionError(
                    'group snapshot is missing for a priority_group with durable history'
                )
            return AlarmRuntimeGroup(
                state=GroupLifecycleState(priority_group=priority_group),
                snapshot=None,
            )
        return AlarmRuntimeGroup(
            state=decode_group_runtime_snapshot(snapshot, planned_alarms=planned_alarms),
            snapshot=snapshot,
        )

    def commit_batch(
        self,
        context: JobRuntimeContext,
        materializations: Sequence[GroupCommitMaterialization],
    ) -> CommitBatchResult:
        if isinstance(materializations, str | bytes) or not isinstance(materializations, Sequence):
            raise TypeError('materializations must be a sequence')
        if not materializations:
            raise ValueError('materializations must not be empty')
        records = []
        for materialization in materializations:
            if not isinstance(materialization, GroupCommitMaterialization):
                raise TypeError('materializations must contain GroupCommitMaterialization values')
            previous_snapshot = self.durability.persistence.read_snapshot(
                materialization.commit.priority_group
            )
            records.append(
                compose_engine_commit_record(
                    materialization,
                    previous_snapshot=previous_snapshot,
                )
            )
        return self.durability.commit_batch(context, records)

    def _has_durable_group_history(self, priority_group: str) -> bool:
        persistence = self.durability.persistence
        if persistence.read_head().durable is None:
            return False
        return any(
            entry.record.commit.priority_group == priority_group
            for entry in persistence.read_durable_records()
        )


def build_alarm_runtime_composition(
    *,
    runtime_configuration: RuntimeConfiguration,
) -> AlarmRuntimeComposition:
    if not isinstance(runtime_configuration, RuntimeConfiguration):
        raise TypeError('runtime_configuration must be a RuntimeConfiguration')
    persistence = AlarmPersistence(shared_volume_path=runtime_configuration.volume_path)
    return AlarmRuntimeComposition(
        runtime_configuration=runtime_configuration,
        durability=AlarmRuntimeDurability(persistence=persistence),
    )
