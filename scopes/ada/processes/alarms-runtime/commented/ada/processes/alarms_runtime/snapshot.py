# Codifica e hidrata GroupRuntimeSnapshot; sólo permite alarmas ausentes de configuración cuando conservan exclusivamente un DeactivationEffect vigente.
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from ada.alarms.core import (
    AlarmEpisode,
    AlarmIdentity,
    AlarmOccurrence,
    AlarmRuntimeState,
    AlarmStatus,
    DeactivationEffect,
    EngineCommit,
    GroupLifecycleState,
    ManagementEffect,
    PendingToolAssignment,
    PlannedAlarm,
    RuntimeEvaluationState,
    TechnicalHold,
    ToolAssignment,
)
from ada.alarms.persistence import (
    GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION,
    GroupRuntimeSnapshot,
)


# Contrato AlarmRuntimeCompositionError: agrupa datos y valida invariantes cerca de su frontera.
class AlarmRuntimeCompositionError(ValueError):
    pass


# Operación encode_group_runtime_snapshot: expone una transformación explícita sin estado global.
def encode_group_runtime_snapshot(
    state: GroupLifecycleState,
    *,
    commit: EngineCommit,
    previous_snapshot: GroupRuntimeSnapshot | None,
) -> GroupRuntimeSnapshot:
    if not isinstance(state, GroupLifecycleState):
        raise TypeError('state must be a GroupLifecycleState')
    if not isinstance(commit, EngineCommit):
        raise TypeError('commit must be an EngineCommit')
    if state.priority_group != commit.priority_group:
        raise AlarmRuntimeCompositionError('state priority_group must match commit priority_group')
    previous_document = _validated_previous_snapshot(commit, previous_snapshot)
    previous_alarms = {} if previous_document is None else previous_document['alarms']
    updated_keys = {identity.canonical_key for identity in commit.runtime_state_updates}
    alarms: dict[str, dict[str, Any]] = {}
    for alarm in state.alarms:
        if _is_neutral(alarm):
            continue
        alarm_key = alarm.alarm_identity.canonical_key
        previous_alarm = previous_alarms.get(alarm_key)
        if alarm_key in updated_keys:
            alarm_commit_id = commit.commit_id
        elif previous_alarm is not None:
            alarm_commit_id = previous_alarm['last_commit_id']
        else:
            raise AlarmRuntimeCompositionError(
                f'alarm runtime state has no commit provenance: {alarm_key}'
            )
        alarms[alarm_key] = _encode_alarm_state(alarm, last_commit_id=alarm_commit_id)
    _validate_runtime_state_updates(
        previous_alarms=previous_alarms,
        next_alarms=alarms,
        updated_keys=updated_keys,
    )
    episode = _encode_episode(state.episode)
    _validate_episode_transition(
        previous_document=previous_document,
        next_episode=episode,
        commit=commit,
    )
    document: dict[str, Any] = {
        'snapshot_schema_version': GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION,
        'priority_group': state.priority_group,
        'last_commit_id': commit.commit_id,
        'alarms': alarms,
    }
    if episode is not None:
        document['episode'] = episode
    state_basis = _resolve_state_basis(
        state=state,
        commit=commit,
        previous_document=previous_document,
    )
    if state_basis is not None:
        document['state_basis'] = state_basis
    return GroupRuntimeSnapshot(document)


# Hidrata hot state con la configuración actual y admite sólo el caso huérfano de deactivation-only.
def decode_group_runtime_snapshot(
    snapshot: GroupRuntimeSnapshot,
    *,
    planned_alarms: Sequence[PlannedAlarm],
) -> GroupLifecycleState:
    if not isinstance(snapshot, GroupRuntimeSnapshot):
        raise TypeError('snapshot must be a GroupRuntimeSnapshot')
    plans = _plan_registry(planned_alarms)
    document = snapshot.as_document()
    episode_document = document.get('episode')
    episode = (
        None
        if episode_document is None
        else AlarmEpisode(
            episode_id=episode_document['episode_id'],
            priority_group=snapshot.priority_group,
            started_at=_parse_utc_timestamp(episode_document['started_at']),
        )
    )
    alarms: list[AlarmRuntimeState] = []
    for alarm_key, alarm_document in sorted(document['alarms'].items()):
        plan = plans.get(alarm_key)
        identity = plan.identity if plan is not None else _identity_from_canonical_key(alarm_key)
        if plan is not None and plan.priority_group != snapshot.priority_group:
            raise AlarmRuntimeCompositionError(
                f'snapshot alarm_key belongs to a different priority_group: {alarm_key}'
            )
        alarm = _decode_alarm_state(
            identity=identity,
            document=alarm_document,
            episode=episode,
        )
        if plan is None and not _is_orphan_deactivation_state(alarm):
            raise AlarmRuntimeCompositionError(
                f'snapshot alarm_key is not present in current configuration: {alarm_key}'
            )
        alarms.append(alarm)
    try:
        return GroupLifecycleState(
            priority_group=snapshot.priority_group,
            episode=episode,
            alarms=tuple(alarms),
        )
    except (TypeError, ValueError) as error:
        raise AlarmRuntimeCompositionError('snapshot cannot hydrate a valid group state') from error


# Auxiliar _validated_previous_snapshot: mantiene una responsabilidad interna acotada y determinista.
def _validated_previous_snapshot(
    commit: EngineCommit,
    previous_snapshot: GroupRuntimeSnapshot | None,
) -> dict[str, Any] | None:
    if previous_snapshot is not None and not isinstance(previous_snapshot, GroupRuntimeSnapshot):
        raise TypeError('previous_snapshot must be a GroupRuntimeSnapshot or None')
    if commit.previous_commit_id is None:
        if previous_snapshot is not None:
            raise AlarmRuntimeCompositionError(
                'initial commit must not be composed over an existing group snapshot'
            )
        return None
    if previous_snapshot is None:
        raise AlarmRuntimeCompositionError('previous group snapshot is required for chained commit')
    if previous_snapshot.priority_group != commit.priority_group:
        raise AlarmRuntimeCompositionError(
            'previous snapshot priority_group must match commit priority_group'
        )
    if previous_snapshot.last_commit_id != commit.previous_commit_id:
        raise AlarmRuntimeCompositionError(
            'previous snapshot last_commit_id must match commit previous_commit_id'
        )
    return previous_snapshot.as_document()


# Auxiliar _validate_runtime_state_updates: mantiene una responsabilidad interna acotada y determinista.
def _validate_runtime_state_updates(
    *,
    previous_alarms: Mapping[str, Any],
    next_alarms: Mapping[str, Any],
    updated_keys: set[str],
) -> None:
    actual_changes: set[str] = set(previous_alarms) ^ set(next_alarms)
    for alarm_key in set(previous_alarms) & set(next_alarms):
        previous_payload = dict(previous_alarms[alarm_key])
        next_payload = dict(next_alarms[alarm_key])
        previous_payload.pop('last_commit_id', None)
        next_payload.pop('last_commit_id', None)
        if previous_payload != next_payload:
            actual_changes.add(alarm_key)
    if actual_changes != updated_keys:
        raise AlarmRuntimeCompositionError(
            'runtime_state_updates must exactly match persisted alarm hot-state changes'
        )


# Auxiliar _validate_episode_transition: mantiene una responsabilidad interna acotada y determinista.
def _validate_episode_transition(
    *,
    previous_document: Mapping[str, Any] | None,
    next_episode: Mapping[str, str] | None,
    commit: EngineCommit,
) -> None:
    previous_episode = None if previous_document is None else previous_document.get('episode')
    if previous_episode == next_episode:
        if commit.episode_change is not None:
            raise AlarmRuntimeCompositionError(
                'episode_change must be absent when the persisted episode does not change'
            )
        return
    if previous_episode is None and next_episode is not None:
        expected_kind = 'STARTED'
        expected_episode_id = next_episode['episode_id']
    elif previous_episode is not None and next_episode is None:
        expected_kind = 'CLOSED'
        expected_episode_id = previous_episode['episode_id']
    else:
        raise AlarmRuntimeCompositionError(
            'one engine commit must not replace an open episode with another episode'
        )
    change = commit.episode_change
    if change is None or change.kind != expected_kind or change.episode_id != expected_episode_id:
        raise AlarmRuntimeCompositionError(
            'episode_change must exactly match the persisted group episode transition'
        )


# Auxiliar _resolve_state_basis: mantiene una responsabilidad interna acotada y determinista.
def _resolve_state_basis(
    *,
    state: GroupLifecycleState,
    commit: EngineCommit,
    previous_document: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    if state.episode is None and not any(not _is_neutral(alarm) for alarm in state.alarms):
        return None
    if commit.runtime_state_updates or commit.episode_change is not None:
        return {
            'alarm_configuration_revision': commit.alarm_configuration_revision,
            'tool_registry_revision': commit.tool_registry_revision,
        }
    if previous_document is None:
        return None
    previous_basis = previous_document.get('state_basis')
    if previous_basis is None:
        return None
    return dict(previous_basis)


# Auxiliar _encode_episode: mantiene una responsabilidad interna acotada y determinista.
def _encode_episode(episode: AlarmEpisode | None) -> dict[str, str] | None:
    if episode is None:
        return None
    return {
        'episode_id': episode.episode_id,
        'started_at': _utc_text(episode.started_at),
    }


# Auxiliar _encode_alarm_state: mantiene una responsabilidad interna acotada y determinista.
def _encode_alarm_state(
    alarm: AlarmRuntimeState,
    *,
    last_commit_id: str,
) -> dict[str, Any]:
    document: dict[str, Any] = {'last_commit_id': last_commit_id}
    if alarm.occurrence is not None:
        if alarm.last_evaluation is None or alarm.management_cycle is None:
            raise AlarmRuntimeCompositionError('open occurrence is missing durable runtime fields')
        occurrence: dict[str, Any] = {
            'occurrence_id': alarm.occurrence.occurrence_id,
            'started_at': _utc_text(alarm.occurrence.started_at),
            'configuration_revision_at_start': alarm.occurrence.alarm_configuration_revision,
            'tool_registry_revision_at_start': alarm.occurrence.tool_registry_revision,
            'last_evaluation': _encode_last_evaluation(alarm.last_evaluation),
            'management_cycle': alarm.management_cycle,
            'assignments': {
                assignment.tool_key: {'assigned_at': _utc_text(assignment.assigned_at)}
                for assignment in alarm.assignments
            },
            'pending_assignments': {
                pending.tool_key: {'due_at': _utc_text(pending.due_at)}
                for pending in alarm.pending_assignments
            },
        }
        if alarm.technical_hold is not None:
            occurrence['technical_hold'] = {
                'started_at': _utc_text(alarm.technical_hold.started_at),
                'due_at': _utc_text(alarm.technical_hold.due_at),
            }
        if alarm.next_evidence_due_at is not None:
            occurrence['next_evidence_due_at'] = _utc_text(alarm.next_evidence_due_at)
        document['occurrence'] = occurrence
    if alarm.management_effect is not None:
        document['management_effect'] = {
            'effect_id': alarm.management_effect.effect_id,
            'source_occurrence_id': alarm.management_effect.source_occurrence_id,
            'effective_at': _utc_text(alarm.management_effect.effective_at),
            'reappearance_due_at': _utc_text(alarm.management_effect.reappearance_due_at),
        }
    if alarm.deactivation_effect is not None:
        document['deactivation_effect'] = {
            'effect_id': alarm.deactivation_effect.effect_id,
            'effective_from': _utc_text(alarm.deactivation_effect.effective_from),
            'effective_until': _utc_text(alarm.deactivation_effect.effective_until),
        }
    return document


# Auxiliar _encode_last_evaluation: mantiene una responsabilidad interna acotada y determinista.
def _encode_last_evaluation(evaluation: RuntimeEvaluationState) -> dict[str, str]:
    document = {
        'status': evaluation.status.value,
        'evaluated_at': _utc_text(evaluation.evaluated_at),
    }
    if evaluation.error_key is not None:
        document['error_key'] = evaluation.error_key
    return document


# Auxiliar _decode_alarm_state: mantiene una responsabilidad interna acotada y determinista.
def _decode_alarm_state(
    *,
    identity: AlarmIdentity,
    document: Mapping[str, Any],
    episode: AlarmEpisode | None,
) -> AlarmRuntimeState:
    occurrence_document = document.get('occurrence')
    occurrence = None
    last_evaluation = None
    technical_hold = None
    management_cycle = None
    assignments: tuple[ToolAssignment, ...] = ()
    pending_assignments: tuple[PendingToolAssignment, ...] = ()
    next_evidence_due_at = None
    if occurrence_document is not None:
        if episode is None:
            raise AlarmRuntimeCompositionError('snapshot occurrence requires an open group episode')
        occurrence = AlarmOccurrence(
            occurrence_id=occurrence_document['occurrence_id'],
            alarm_identity=identity,
            episode_id=episode.episode_id,
            started_at=_parse_utc_timestamp(occurrence_document['started_at']),
            alarm_configuration_revision=occurrence_document['configuration_revision_at_start'],
            tool_registry_revision=occurrence_document['tool_registry_revision_at_start'],
        )
        evaluation_document = occurrence_document['last_evaluation']
        last_evaluation = RuntimeEvaluationState(
            status=AlarmStatus(evaluation_document['status']),
            evaluated_at=_parse_utc_timestamp(evaluation_document['evaluated_at']),
            error_key=evaluation_document.get('error_key'),
        )
        hold_document = occurrence_document.get('technical_hold')
        if hold_document is not None:
            technical_hold = TechnicalHold(
                started_at=_parse_utc_timestamp(hold_document['started_at']),
                due_at=_parse_utc_timestamp(hold_document['due_at']),
            )
        management_cycle = occurrence_document['management_cycle']
        assignments = tuple(
            ToolAssignment(
                tool_key=tool_key,
                assigned_at=_parse_utc_timestamp(payload['assigned_at']),
            )
            for tool_key, payload in sorted(occurrence_document['assignments'].items())
        )
        pending_assignments = tuple(
            PendingToolAssignment(
                tool_key=tool_key,
                due_at=_parse_utc_timestamp(payload['due_at']),
            )
            for tool_key, payload in sorted(occurrence_document['pending_assignments'].items())
        )
        due_at = occurrence_document.get('next_evidence_due_at')
        if due_at is not None:
            next_evidence_due_at = _parse_utc_timestamp(due_at)
    management_document = document.get('management_effect')
    management_effect = (
        None
        if management_document is None
        else ManagementEffect(
            effect_id=management_document['effect_id'],
            source_occurrence_id=management_document['source_occurrence_id'],
            effective_at=_parse_utc_timestamp(management_document['effective_at']),
            reappearance_due_at=_parse_utc_timestamp(management_document['reappearance_due_at']),
        )
    )
    deactivation_document = document.get('deactivation_effect')
    deactivation_effect = (
        None
        if deactivation_document is None
        else DeactivationEffect(
            effect_id=deactivation_document['effect_id'],
            effective_from=_parse_utc_timestamp(deactivation_document['effective_from']),
            effective_until=_parse_utc_timestamp(deactivation_document['effective_until']),
        )
    )
    try:
        return AlarmRuntimeState(
            alarm_identity=identity,
            occurrence=occurrence,
            last_evaluation=last_evaluation,
            technical_hold=technical_hold,
            management_cycle=management_cycle,
            management_effect=management_effect,
            deactivation_effect=deactivation_effect,
            assignments=assignments,
            pending_assignments=pending_assignments,
            next_evidence_due_at=next_evidence_due_at,
        )
    except (TypeError, ValueError) as error:
        raise AlarmRuntimeCompositionError(
            f'snapshot alarm state is invalid for current Core contract: {identity.canonical_key}'
        ) from error


# Delimita estrictamente el único hot state permitido para una alarma que ya no existe en la configuración vigente.
def _is_orphan_deactivation_state(alarm: AlarmRuntimeState) -> bool:
    return (
        alarm.occurrence is None
        and alarm.management_effect is None
        and alarm.deactivation_effect is not None
    )


# Auxiliar _identity_from_canonical_key: mantiene una responsabilidad interna acotada y determinista.
def _identity_from_canonical_key(value: str) -> AlarmIdentity:
    if not isinstance(value, str) or not value.strip():
        raise AlarmRuntimeCompositionError('snapshot alarm_key must be a non-empty string')
    parts = value.split('/')
    if len(parts) != 2 or not all(part.strip() for part in parts):
        raise AlarmRuntimeCompositionError('snapshot alarm_key is invalid')
    return AlarmIdentity(family_key=parts[0].strip(), alarm_key=parts[1].strip())


# Auxiliar _plan_registry: mantiene una responsabilidad interna acotada y determinista.
def _plan_registry(planned_alarms: Sequence[PlannedAlarm]) -> dict[str, PlannedAlarm]:
    if isinstance(planned_alarms, str | bytes) or not isinstance(planned_alarms, Sequence):
        raise TypeError('planned_alarms must be a sequence')
    plans: dict[str, PlannedAlarm] = {}
    for plan in planned_alarms:
        if not isinstance(plan, PlannedAlarm):
            raise TypeError('planned_alarms must contain PlannedAlarm values')
        key = plan.identity.canonical_key
        if key in plans:
            raise AlarmRuntimeCompositionError(
                f'current configuration contains duplicate canonical alarm identity: {key}'
            )
        plans[key] = plan
    return plans


# Auxiliar _is_neutral: mantiene una responsabilidad interna acotada y determinista.
def _is_neutral(alarm: AlarmRuntimeState) -> bool:
    return (
        alarm.occurrence is None
        and alarm.management_effect is None
        and alarm.deactivation_effect is None
    )


# Auxiliar _parse_utc_timestamp: mantiene una responsabilidad interna acotada y determinista.
def _parse_utc_timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError('timestamp must be a string')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise AlarmRuntimeCompositionError('timestamp is not a valid ISO-8601 value') from error
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
    ):
        raise AlarmRuntimeCompositionError('timestamp must be UTC')
    return parsed.astimezone(UTC)


# Auxiliar _utc_text: mantiene una responsabilidad interna acotada y determinista.
def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')
