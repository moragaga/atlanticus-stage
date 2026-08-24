from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

from ada.alarms.persistence import (
    GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION,
    EngineCommitMetadata,
    EngineCommitRecord,
    GroupRuntimeSnapshot,
)
from atlanticus.runtime import JobDefinition, JobRuntimeContext, RuntimeConfiguration


@contextmanager
def mutation_fence() -> Iterator[None]:
    yield


def build_context(volume_path: Path) -> JobRuntimeContext:
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada-alarms-runtime-test',
            'VOLUMEN_PATH': str(volume_path),
        }
    )
    context = JobRuntimeContext.create(
        definition=JobDefinition(
            module_name='ada.processes.alarms_runtime',
            service_name='alarms-runtime',
            job_key='alarms-runtime',
            iteration_timeout_seconds=10,
            execution_timeout_seconds=30,
            shutdown_grace_seconds=5,
            lease_timeout_seconds=10,
            lease_renew_seconds=3,
            lease_wait_seconds=0,
            resource_sample_seconds=1,
        ),
        configuration=configuration,
        run_id='11111111-1111-1111-1111-111111111111',
        correlation_id='22222222-2222-2222-2222-222222222222',
    )
    context._bind_lease_authority(
        generation=1,
        checker=lambda: None,
        fence=mutation_fence,
    )
    return context


def build_record(
    *,
    commit_id: str = 'C1',
    previous_commit_id: str | None = None,
    priority_group: str = 'crusher_pressure',
    evaluated_at: str = '2026-08-24T12:00:00Z',
) -> EngineCommitRecord:
    snapshot = build_snapshot(priority_group=priority_group, commit_id=commit_id)
    commit = EngineCommitMetadata(
        commit_id=commit_id,
        cycle_id='20260824T120000000000Z',
        priority_group=priority_group,
        previous_commit_id=previous_commit_id,
        evaluated_at=evaluated_at,
        committed_at=evaluated_at,
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        runtime_artifact_version='ada-alarms-runtime/0.1.0',
        affected_alarms=('crusher_pressure_risk',),
    )
    return EngineCommitRecord.create(
        commit=commit,
        snapshot_after=snapshot,
        records={
            'journey_events': [
                {
                    'journey_event_id': f'J-{commit_id}',
                    'alarm_key': 'crusher_pressure_risk',
                    'occurred_at': evaluated_at,
                }
            ]
        },
    )


def build_snapshot(
    *,
    priority_group: str,
    commit_id: str,
) -> GroupRuntimeSnapshot:
    evaluated_at = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
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
                'started_at': _utc_text(evaluated_at),
            },
            'alarms': {
                'crusher_pressure_risk': {
                    'last_commit_id': commit_id,
                    'occurrence': {
                        'occurrence_id': f'O-{priority_group}',
                        'started_at': _utc_text(evaluated_at),
                        'configuration_revision_at_start': 'R42',
                        'tool_registry_revision_at_start': 'T18',
                        'last_evaluation': {
                            'status': 'ACTIVE',
                            'evaluated_at': _utc_text(evaluated_at),
                        },
                        'management_cycle': 1,
                        'assignments': {
                            'io': {'assigned_at': _utc_text(evaluated_at)},
                        },
                        'pending_assignments': {
                            'strategic': {'due_at': _utc_text(evaluated_at + timedelta(minutes=30))}
                        },
                        'next_evidence_due_at': _utc_text(evaluated_at + timedelta(minutes=5)),
                    },
                }
            },
        }
    )


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')
