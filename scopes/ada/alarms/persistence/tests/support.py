from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Iterator

from ada.alarms.persistence import (
    GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION,
    EngineCommitMetadata,
    EngineCommitRecord,
    GroupRuntimeSnapshot,
)


@contextmanager
def mutation_fence() -> Iterator[None]:
    yield


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def build_snapshot(
    *,
    priority_group: str = 'crusher_pressure',
    commit_id: str = 'C1',
    alarm_key: str = 'crusher_pressure_risk',
    error_key: str | None = None,
) -> GroupRuntimeSnapshot:
    evaluated_at = datetime(2026, 8, 23, 20, 0, tzinfo=UTC)
    last_evaluation: dict[str, object] = {
        'status': 'ACTIVE',
        'evaluated_at': utc_text(evaluated_at),
    }
    if error_key is not None:
        last_evaluation['error_key'] = error_key
    return GroupRuntimeSnapshot(
        {
            'snapshot_schema_version': GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION,
            'priority_group': priority_group,
            'last_commit_id': commit_id,
            'state_basis': {
                'alarm_configuration_revision': 'R42',
                'tool_registry_revision': 'T18',
            },
            'episode': {
                'episode_id': f'E-{priority_group}',
                'started_at': utc_text(evaluated_at),
            },
            'alarms': {
                alarm_key: {
                    'last_commit_id': commit_id,
                    'occurrence': {
                        'occurrence_id': f'O-{priority_group}',
                        'started_at': utc_text(evaluated_at),
                        'configuration_revision_at_start': 'R42',
                        'tool_registry_revision_at_start': 'T18',
                        'last_evaluation': last_evaluation,
                        'management_cycle': 1,
                        'assignments': {
                            'io': {'assigned_at': utc_text(evaluated_at)},
                        },
                        'pending_assignments': {
                            'strategic': {'due_at': utc_text(evaluated_at + timedelta(minutes=30))},
                        },
                        'next_evidence_due_at': utc_text(evaluated_at + timedelta(minutes=5)),
                    },
                }
            },
        }
    )


def build_record(
    *,
    commit_id: str = 'C1',
    previous_commit_id: str | None = None,
    priority_group: str = 'crusher_pressure',
    cycle_id: str = '20260823T200000000000Z',
    evaluated_at: str = '2026-08-23T20:00:00Z',
    alarm_key: str = 'crusher_pressure_risk',
    error_key: str | None = None,
) -> EngineCommitRecord:
    commit = EngineCommitMetadata(
        commit_id=commit_id,
        cycle_id=cycle_id,
        priority_group=priority_group,
        previous_commit_id=previous_commit_id,
        evaluated_at=evaluated_at,
        committed_at=evaluated_at,
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        runtime_artifact_version='ada-alarms-runtime/0.1.0',
        affected_alarms=(alarm_key,),
    )
    return EngineCommitRecord.create(
        commit=commit,
        snapshot_after=build_snapshot(
            priority_group=priority_group,
            commit_id=commit_id,
            alarm_key=alarm_key,
            error_key=error_key,
        ),
        records={
            'journey_events': [
                {
                    'journey_event_id': f'J-{commit_id}',
                    'alarm_key': alarm_key,
                    'occurred_at': evaluated_at,
                }
            ]
        },
    )
