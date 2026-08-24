from __future__ import annotations

# Este adapter convierte una materialización funcional de Core en la unidad física EngineCommitRecord.v1.
# Persistence sigue siendo autoridad del hash y de la validación física del registro.

from datetime import UTC, datetime

from ada.alarms.core import GroupCommitMaterialization
from ada.alarms.persistence import EngineCommitMetadata, EngineCommitRecord, GroupRuntimeSnapshot
from ada.processes.alarms_runtime.snapshot import encode_group_runtime_snapshot


def compose_engine_commit_record(
    materialization: GroupCommitMaterialization,
    *,
    previous_snapshot: GroupRuntimeSnapshot | None,
) -> EngineCommitRecord:
    if not isinstance(materialization, GroupCommitMaterialization):
        raise TypeError('materialization must be a GroupCommitMaterialization')
    commit = materialization.commit
    snapshot = encode_group_runtime_snapshot(
        materialization.state,
        commit=commit,
        previous_snapshot=previous_snapshot,
    )
    metadata = EngineCommitMetadata(
        commit_id=commit.commit_id,
        cycle_id=commit.cycle_id,
        priority_group=commit.priority_group,
        previous_commit_id=commit.previous_commit_id,
        evaluated_at=_utc_text(commit.evaluated_at),
        committed_at=_utc_text(commit.committed_at),
        alarm_configuration_revision=commit.alarm_configuration_revision,
        tool_registry_revision=commit.tool_registry_revision,
        runtime_artifact_version=commit.runtime_artifact_version,
        affected_alarms=tuple(identity.canonical_key for identity in commit.affected_alarms),
    )
    return EngineCommitRecord.create(
        commit=metadata,
        snapshot_after=snapshot,
        records=materialization.records.as_document(),
    )


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')
