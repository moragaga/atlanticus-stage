from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

from ada.alarms.core import (
    AlarmEpisode,
    AlarmIdentity,
    AlarmKind,
    AlarmOccurrence,
    AlarmRouting,
    AlarmRuntimeState,
    AlarmStatus,
    Criticality,
    EngineCommit,
    EngineCommitRecords,
    EpisodeChangeReference,
    GroupCommitMaterialization,
    GroupLifecycleState,
    InputKind,
    InputReceipt,
    PendingToolAssignment,
    PlannedAlarm,
    RuntimeEvaluationState,
    TechnicalHold,
    ToolAssignment,
    commit_id_for,
    cycle_id_for,
)
from ada.alarms.persistence import (
    GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION,
    EngineCommitMetadata,
    EngineCommitRecord,
    GroupRuntimeSnapshot,
)
from atlanticus.runtime import JobDefinition, JobRuntimeContext, RuntimeConfiguration

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


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
        runtime_artifact_version='ada-alarms-runtime/0.2.0',
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
    evaluated_at = NOW
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


def identity(alarm_key: str = 'risk') -> AlarmIdentity:
    return AlarmIdentity(family_key='mill', alarm_key=alarm_key)


def plan(alarm_key: str = 'risk', *, priority_order: int = 1) -> PlannedAlarm:
    return PlannedAlarm(
        identity=identity(alarm_key),
        kind=AlarmKind.RISK,
        criticality=Criticality.C3,
        priority_group='mill-feed',
        priority_order=priority_order,
        delivery_enabled=True,
        evaluator_key='threshold',
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        routing=AlarmRouting(origin_tool_key='io'),
    )


def active_runtime_state(
    alarm_key: str = 'risk',
    *,
    at: datetime = NOW,
) -> AlarmRuntimeState:
    alarm_identity = identity(alarm_key)
    occurrence = AlarmOccurrence(
        occurrence_id=f'O-{alarm_key}',
        alarm_identity=alarm_identity,
        episode_id='E1',
        started_at=at,
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
    )
    return AlarmRuntimeState(
        alarm_identity=alarm_identity,
        occurrence=occurrence,
        last_evaluation=RuntimeEvaluationState(
            status=AlarmStatus.ACTIVE,
            evaluated_at=at,
        ),
        management_cycle=1,
        assignments=(ToolAssignment(tool_key='io', assigned_at=at),),
        pending_assignments=(
            PendingToolAssignment(tool_key='strategic', due_at=at + timedelta(minutes=30)),
        ),
        next_evidence_due_at=at + timedelta(minutes=5),
    )


def error_runtime_state(
    alarm_key: str = 'risk',
    *,
    at: datetime = NOW,
) -> AlarmRuntimeState:
    alarm_identity = identity(alarm_key)
    occurrence = AlarmOccurrence(
        occurrence_id=f'O-{alarm_key}',
        alarm_identity=alarm_identity,
        episode_id='E1',
        started_at=at - timedelta(minutes=1),
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
    )
    return AlarmRuntimeState(
        alarm_identity=alarm_identity,
        occurrence=occurrence,
        last_evaluation=RuntimeEvaluationState(
            status=AlarmStatus.ERROR,
            evaluated_at=at,
            error_key='pi-quality',
        ),
        technical_hold=TechnicalHold(
            started_at=at,
            due_at=at + timedelta(minutes=5),
        ),
        management_cycle=1,
        assignments=(ToolAssignment(tool_key='io', assigned_at=occurrence.started_at),),
    )


def group_state(*alarms: AlarmRuntimeState, started_at: datetime = NOW) -> GroupLifecycleState:
    return GroupLifecycleState(
        priority_group='mill-feed',
        episode=AlarmEpisode(
            episode_id='E1',
            priority_group='mill-feed',
            started_at=started_at,
        ),
        alarms=tuple(sorted(alarms, key=lambda item: item.alarm_identity)),
    )


def materialization(
    state: GroupLifecycleState,
    *,
    at: datetime = NOW,
    previous_commit_id: str | None = None,
    runtime_state_updates: tuple[AlarmIdentity, ...] | None = None,
    affected_alarms: tuple[AlarmIdentity, ...] | None = None,
    alarm_configuration_revision: str = 'R42',
    tool_registry_revision: str = 'T18',
    receipt_input_id: str | None = None,
) -> GroupCommitMaterialization:
    cycle_id = cycle_id_for(at)
    commit_id = commit_id_for(cycle_id, state.priority_group)
    updates = runtime_state_updates or ()
    affected = affected_alarms or updates
    records = EngineCommitRecords()
    receipt_ids: tuple[str, ...] = ()
    if receipt_input_id is not None:
        receipt = InputReceipt(
            input_id=receipt_input_id,
            input_kind=InputKind.MANAGEMENT,
            commit_id=commit_id,
            applied_at=at + timedelta(seconds=1),
            outcome='LATE',
        )
        records = EngineCommitRecords(input_receipts=(receipt,))
        receipt_ids = (receipt.receipt_id,)
        if not affected:
            raise ValueError('receipt materialization requires affected_alarms')
    commit = EngineCommit(
        commit_id=commit_id,
        cycle_id=cycle_id,
        priority_group=state.priority_group,
        previous_commit_id=previous_commit_id,
        evaluated_at=at,
        committed_at=at + timedelta(seconds=1),
        alarm_configuration_revision=alarm_configuration_revision,
        tool_registry_revision=tool_registry_revision,
        runtime_artifact_version='ada-alarms-runtime/0.2.0',
        affected_alarms=affected,
        runtime_state_updates=updates,
        occurrence_changes=(),
        episode_change=(
            EpisodeChangeReference(episode_id=state.episode.episode_id, kind='STARTED')
            if previous_commit_id is None and state.episode is not None
            else None
        ),
        journey_event_ids=(),
        evidence_record_ids=(),
        management_effect_ids=(),
        deactivation_effect_ids=(),
        assignment_change_ids=(),
        receipt_ids=receipt_ids,
    )
    return GroupCommitMaterialization(state=state, commit=commit, records=records)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')
