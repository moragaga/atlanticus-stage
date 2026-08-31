from __future__ import annotations

from collections.abc import Mapping, Sequence
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

    def reconcile_drain(self, context: JobRuntimeContext) -> RecoveryResult:
        return self.durability.reconcile_drain(context)

    def load_group(
        self,
        priority_group: str,
        *,
        planned_alarms: Sequence[PlannedAlarm],
    ) -> AlarmRuntimeGroup:
        return self.load_groups({priority_group: planned_alarms})[priority_group]

    def load_groups(
        self,
        planned_alarms_by_group: Mapping[str, Sequence[PlannedAlarm]],
    ) -> dict[str, AlarmRuntimeGroup]:
        if not isinstance(planned_alarms_by_group, Mapping):
            raise TypeError('planned_alarms_by_group must be a mapping')
        snapshots: dict[str, GroupRuntimeSnapshot | None] = {}
        for priority_group in planned_alarms_by_group:
            self._validate_priority_group(priority_group)
            snapshots[priority_group] = self.durability.persistence.read_snapshot(priority_group)
        missing_groups = tuple(
            priority_group for priority_group, snapshot in snapshots.items() if snapshot is None
        )
        durable_history_groups = (
            frozenset() if not missing_groups else self._read_durable_history_groups()
        )
        return {
            priority_group: self._compose_group(
                priority_group,
                planned_alarms=planned_alarms,
                snapshot=snapshots[priority_group],
                durable_history_groups=durable_history_groups,
            )
            for priority_group, planned_alarms in planned_alarms_by_group.items()
        }

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

    def _compose_group(
        self,
        priority_group: str,
        *,
        planned_alarms: Sequence[PlannedAlarm],
        snapshot: GroupRuntimeSnapshot | None,
        durable_history_groups: frozenset[str],
    ) -> AlarmRuntimeGroup:
        if snapshot is None:
            if priority_group in durable_history_groups:
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

    def _has_durable_group_history(self, priority_group: str) -> bool:
        self._validate_priority_group(priority_group)
        return priority_group in self._read_durable_history_groups()

    def _read_durable_history_groups(self) -> frozenset[str]:
        persistence = self.durability.persistence
        if persistence.read_head().durable is None:
            return frozenset()
        return frozenset(
            entry.record.commit.priority_group for entry in persistence.read_durable_records()
        )

    @staticmethod
    def _validate_priority_group(priority_group: str) -> None:
        if not isinstance(priority_group, str):
            raise TypeError('priority_group must be a string')
        if not priority_group.strip():
            raise ValueError('priority_group must not be empty')


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
