from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ada.alarms.core.errors import AlarmContractError
from ada.alarms.core.models import (
    AlarmIdentity,
    AlarmPriorityDecision,
    AssignmentChangeKind,
    DeactivationEffectChangeKind,
    GroupLifecycleDecision,
    GroupLifecycleState,
    GroupPriorityResolution,
    ManagementActionOutcome,
    ManagementEffectChangeKind,
    OccurrenceChangeKind,
    OccurrenceClosureReason,
    PriorityDisposition,
    TechnicalHoldChangeKind,
)


@dataclass(frozen=True, slots=True)
class JourneyEvent:
    event_id: str
    event_key: str
    effective_at: datetime
    alarm_identity: AlarmIdentity
    occurrence_id: str | None = None
    episode_id: str | None = None
    tool_key: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.event_id, 'event_id')
        _require_non_empty_string(self.event_key, 'event_key')
        _require_utc_datetime(self.effective_at, 'effective_at')
        if not isinstance(self.alarm_identity, AlarmIdentity):
            raise TypeError('alarm_identity must be an AlarmIdentity')
        for name in ('occurrence_id', 'episode_id', 'tool_key'):
            value = getattr(self, name)
            if value is not None:
                _require_non_empty_string(value, name)

    def as_document(self) -> dict[str, str]:
        document = {
            'event_id': self.event_id,
            'event_key': self.event_key,
            'effective_at': _timestamp(self.effective_at),
            'alarm_key': self.alarm_identity.canonical_key,
        }
        if self.occurrence_id is not None:
            document['occurrence_id'] = self.occurrence_id
        if self.episode_id is not None:
            document['episode_id'] = self.episode_id
        if self.tool_key is not None:
            document['tool_key'] = self.tool_key
        return document


def materialize_journey(
    previous_state: GroupLifecycleState,
    decision: GroupLifecycleDecision,
    *,
    state: GroupLifecycleState,
    cycle_at: datetime,
    previous_priority_resolution: GroupPriorityResolution | None = None,
) -> tuple[JourneyEvent, ...]:
    if not isinstance(previous_state, GroupLifecycleState):
        raise TypeError('previous_state must be a GroupLifecycleState')
    if not isinstance(decision, GroupLifecycleDecision):
        raise TypeError('decision must be a GroupLifecycleDecision')
    if not isinstance(state, GroupLifecycleState):
        raise TypeError('state must be a GroupLifecycleState')
    _require_utc_datetime(cycle_at, 'cycle_at')
    events: list[JourneyEvent] = []
    for change in decision.occurrence_changes:
        occurrence = change.occurrence
        effective_at = (
            occurrence.started_at
            if change.kind is OccurrenceChangeKind.STARTED
            else occurrence.ended_at
        )
        if effective_at is None:
            raise AlarmContractError('closed occurrence requires ended_at')
        events.append(
            _journey_event(
                event_key=(
                    'occurrence_started'
                    if change.kind is OccurrenceChangeKind.STARTED
                    else 'occurrence_closed'
                ),
                effective_at=effective_at,
                alarm_identity=occurrence.alarm_identity,
                occurrence_id=occurrence.occurrence_id,
                episode_id=occurrence.episode_id,
            )
        )
    closed_by_occurrence = {
        change.occurrence.occurrence_id: change.occurrence
        for change in decision.occurrence_changes
        if change.kind is OccurrenceChangeKind.CLOSED
    }
    for change in decision.technical_hold_changes:
        if change.kind is TechnicalHoldChangeKind.STARTED:
            event_key = 'technical_hold_started'
        else:
            closed = closed_by_occurrence.get(change.occurrence_id)
            event_key = (
                'technical_hold_expired'
                if closed is not None
                and closed.closure_reason is OccurrenceClosureReason.TECHNICAL_HOLD_EXPIRED
                else 'technical_hold_recovered'
            )
        occurrence = _occurrence_for_identity(
            change.alarm_identity,
            previous_state=previous_state,
            next_state=state,
            occurrence_id=change.occurrence_id,
        )
        events.append(
            _journey_event(
                event_key=event_key,
                effective_at=change.effective_at,
                alarm_identity=change.alarm_identity,
                occurrence_id=change.occurrence_id,
                episode_id=None if occurrence is None else occurrence.episode_id,
            )
        )
    for result in decision.management_action_results:
        event_key = {
            ManagementActionOutcome.EFFECTIVE: 'management_applied',
            ManagementActionOutcome.ADDITIONAL: 'management_response_recorded',
            ManagementActionOutcome.LATE: 'management_late',
        }[result.outcome]
        occurrence = _occurrence_for_identity(
            result.action.alarm_identity,
            previous_state=previous_state,
            next_state=state,
            occurrence_id=result.action.source_occurrence_id,
        )
        events.append(
            _journey_event(
                event_key=event_key,
                effective_at=result.action.source_created_at,
                alarm_identity=result.action.alarm_identity,
                occurrence_id=result.action.source_occurrence_id,
                episode_id=None if occurrence is None else occurrence.episode_id,
                tool_key=result.action.tool_key,
                discriminator=result.action.input_id,
            )
        )
    for change in decision.reappearance_changes:
        occurrence = _occurrence_for_identity(
            change.alarm_identity,
            previous_state=previous_state,
            next_state=state,
            occurrence_id=change.occurrence_id,
        )
        events.append(
            _journey_event(
                event_key='reappeared',
                effective_at=change.effective_at,
                alarm_identity=change.alarm_identity,
                occurrence_id=change.occurrence_id,
                episode_id=None if occurrence is None else occurrence.episode_id,
                discriminator=str(change.management_cycle),
            )
        )
    assignment_event_keys = {
        AssignmentChangeKind.ASSIGNED: 'tool_assigned',
        AssignmentChangeKind.REMOVED: 'tool_assignment_removed',
        AssignmentChangeKind.SCHEDULED: 'tool_assignment_scheduled',
        AssignmentChangeKind.RESCHEDULED: 'tool_assignment_rescheduled',
    }
    for change in decision.assignment_changes:
        occurrence = _occurrence_for_identity(
            change.alarm_identity,
            previous_state=previous_state,
            next_state=state,
        )
        if occurrence is None:
            raise AlarmContractError('assignment change requires an occurrence reference')
        discriminator = change.tool_key
        if change.due_at is not None:
            discriminator = f'{change.tool_key}:{_timestamp(change.due_at)}'
        events.append(
            _journey_event(
                event_key=assignment_event_keys[change.kind],
                effective_at=change.effective_at,
                alarm_identity=change.alarm_identity,
                occurrence_id=occurrence.occurrence_id,
                episode_id=occurrence.episode_id,
                tool_key=change.tool_key,
                discriminator=discriminator,
            )
        )
    for change in decision.deactivation_effect_changes:
        if change.kind is not DeactivationEffectChangeKind.CLEARED:
            continue
        previous = previous_state.get(change.alarm_identity)
        previous_effect = None if previous is None else previous.deactivation_effect
        event_key = (
            'deactivation_expired'
            if previous_effect is not None
            and change.effective_at == previous_effect.effective_until
            else 'deactivation_cleared'
        )
        occurrence = _occurrence_for_identity(
            change.alarm_identity,
            previous_state=previous_state,
            next_state=state,
        )
        events.append(
            _journey_event(
                event_key=event_key,
                effective_at=change.effective_at,
                alarm_identity=change.alarm_identity,
                occurrence_id=None if occurrence is None else occurrence.occurrence_id,
                episode_id=None if occurrence is None else occurrence.episode_id,
                discriminator=None if previous_effect is None else previous_effect.effect_id,
            )
        )
    for result in decision.deactivation_request_results:
        occurrence = _occurrence_for_identity(
            result.action.alarm_identity,
            previous_state=previous_state,
            next_state=state,
            occurrence_id=result.action.source_occurrence_id,
        )
        events.append(
            _journey_event(
                event_key=f'deactivation_request_{result.outcome.value.lower()}',
                effective_at=result.action.source_created_at,
                alarm_identity=result.action.alarm_identity,
                occurrence_id=result.action.source_occurrence_id,
                episode_id=None if occurrence is None else occurrence.episode_id,
                tool_key=result.action.tool_key,
                discriminator=result.action.input_id,
            )
        )
    for result in decision.deactivation_decision_results:
        request = result.deactivation_request
        if request is None:
            continue
        occurrence = _occurrence_for_identity(
            request.alarm_identity,
            previous_state=previous_state,
            next_state=state,
            occurrence_id=request.source_occurrence_id,
        )
        events.append(
            _journey_event(
                event_key=f'deactivation_decision_{result.outcome.value.lower()}',
                effective_at=result.decision.decided_at,
                alarm_identity=request.alarm_identity,
                occurrence_id=request.source_occurrence_id,
                episode_id=None if occurrence is None else occurrence.episode_id,
                discriminator=result.decision.decision_id,
            )
        )
    events.extend(
        _priority_journey_events(
            previous_priority_resolution,
            decision.priority_resolution,
            decision=decision,
            state=state,
            cycle_at=cycle_at,
        )
    )
    return tuple(sorted(events, key=lambda item: (item.effective_at, item.event_id)))


def _priority_journey_events(
    previous: GroupPriorityResolution | None,
    current: GroupPriorityResolution | None,
    *,
    decision: GroupLifecycleDecision,
    state: GroupLifecycleState,
    cycle_at: datetime,
) -> tuple[JourneyEvent, ...]:
    if previous is None or current is None:
        return ()
    previous_map = {item.alarm_identity: item for item in previous.alarms}
    events: list[JourneyEvent] = []
    for item in current.alarms:
        before = previous_map.get(item.alarm_identity)
        if before is None or before.disposition is item.disposition:
            continue
        suppressed_before = before.disposition in {
            PriorityDisposition.ECLIPSED,
            PriorityDisposition.CASCADE_SUPPRESSED,
        }
        suppressed_now = item.disposition in {
            PriorityDisposition.ECLIPSED,
            PriorityDisposition.CASCADE_SUPPRESSED,
        }
        if suppressed_before == suppressed_now:
            continue
        runtime = state.get(item.alarm_identity)
        if runtime is None or runtime.occurrence is None:
            continue
        effective_at = _priority_transition_effective_at(
            item.alarm_identity,
            before=before,
            after=item,
            decision=decision,
            state=state,
            cycle_at=cycle_at,
        )
        events.append(
            _journey_event(
                event_key='priority_suppressed' if suppressed_now else 'priority_released',
                effective_at=effective_at,
                alarm_identity=item.alarm_identity,
                occurrence_id=runtime.occurrence.occurrence_id,
                episode_id=runtime.occurrence.episode_id,
                discriminator=f'{before.disposition.value}:{item.disposition.value}',
            )
        )
    return tuple(events)


def _priority_transition_effective_at(
    identity: AlarmIdentity,
    *,
    before: AlarmPriorityDecision,
    after: AlarmPriorityDecision,
    decision: GroupLifecycleDecision,
    state: GroupLifecycleState,
    cycle_at: datetime,
) -> datetime:
    if after.disposition is PriorityDisposition.CASCADE_SUPPRESSED:
        suppressions = [
            suppression
            for suppression in decision.cascade_suppressions
            if suppression.target_alarm_identity == identity
        ]
        effect_times: list[datetime] = []
        for suppression in suppressions:
            source = state.get(suppression.source_alarm_identity)
            if (
                source is not None
                and source.management_effect is not None
                and source.management_effect.effect_id == suppression.management_effect_id
            ):
                effect_times.append(source.management_effect.effective_at)
        if effect_times:
            return min(effect_times)
    if after.disposition is PriorityDisposition.ECLIPSED:
        blocker_ids = set(after.blocking_alarm_identities)
        blocker_starts = [
            change.occurrence.started_at
            for change in decision.occurrence_changes
            if change.kind is OccurrenceChangeKind.STARTED
            and change.occurrence.alarm_identity in blocker_ids
        ]
        if blocker_starts:
            return min(blocker_starts)
        target_start = next(
            (
                change.occurrence.started_at
                for change in decision.occurrence_changes
                if change.kind is OccurrenceChangeKind.STARTED
                and change.occurrence.alarm_identity == identity
            ),
            None,
        )
        if target_start is not None:
            return target_start
    if before.disposition is PriorityDisposition.CASCADE_SUPPRESSED:
        blocker_ids = set(before.blocking_alarm_identities)
        cleared = [
            change.effective_at
            for change in decision.management_effect_changes
            if change.kind is ManagementEffectChangeKind.CLEARED
            and change.alarm_identity in blocker_ids
        ]
        if cleared:
            return min(cleared)
    if before.disposition is PriorityDisposition.ECLIPSED:
        blocker_ids = set(before.blocking_alarm_identities)
        closed = [
            change.occurrence.ended_at
            for change in decision.occurrence_changes
            if change.kind is OccurrenceChangeKind.CLOSED
            and change.occurrence.alarm_identity in blocker_ids
            and change.occurrence.ended_at is not None
        ]
        if closed:
            return min(closed)
    return cycle_at


def _journey_event(
    *,
    event_key: str,
    effective_at: datetime,
    alarm_identity: AlarmIdentity,
    occurrence_id: str | None = None,
    episode_id: str | None = None,
    tool_key: str | None = None,
    discriminator: str | None = None,
) -> JourneyEvent:
    identity_parts = [
        'journey',
        event_key,
        alarm_identity.canonical_key,
        occurrence_id or '-',
        _timestamp(effective_at),
    ]
    if tool_key is not None:
        identity_parts.append(tool_key)
    if discriminator is not None:
        identity_parts.append(discriminator)
    return JourneyEvent(
        event_id=':'.join(identity_parts),
        event_key=event_key,
        effective_at=effective_at,
        alarm_identity=alarm_identity,
        occurrence_id=occurrence_id,
        episode_id=episode_id,
        tool_key=tool_key,
    )


def _occurrence_for_identity(
    identity: AlarmIdentity,
    *,
    previous_state: GroupLifecycleState,
    next_state: GroupLifecycleState,
    occurrence_id: str | None = None,
):
    for state in (next_state, previous_state):
        runtime = state.get(identity)
        if runtime is None or runtime.occurrence is None:
            continue
        if occurrence_id is None or runtime.occurrence.occurrence_id == occurrence_id:
            return runtime.occurrence
    return None


def _timestamp(value: datetime) -> str:
    _require_utc_datetime(value, 'timestamp')
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def _require_non_empty_string(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f'{name} must be a str')
    if not value.strip():
        raise ValueError(f'{name} must not be empty')


def _require_utc_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f'{name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f'{name} must be UTC')
