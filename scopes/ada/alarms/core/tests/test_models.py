from datetime import UTC, datetime, timedelta

import pytest

from ada.alarms.core import (
    AffectedInputIssue,
    AlarmEpisode,
    AlarmEvaluation,
    AlarmIdentity,
    AlarmKind,
    AlarmOccurrence,
    AlarmRouting,
    AlarmRuntimeState,
    AlarmStatus,
    ConfigurationClosure,
    Criticality,
    EpisodeClosureReason,
    EvaluationContext,
    EvaluationError,
    EvaluationErrorOrigin,
    EvidenceSnapshot,
    GroupLifecycleDecision,
    GroupLifecycleState,
    OccurrenceClosureReason,
    PendingToolAssignment,
    PlannedAlarm,
    RoutingDestination,
    TechnicalHold,
    TechnicalHoldChange,
    TechnicalHoldChangeKind,
    ToolAssignment,
)

from .support import NOW, identity, physical, plan


def test_identity_is_stable_pair() -> None:
    value = AlarmIdentity(family_key='ph', alarm_key='high')
    assert value.canonical_key == 'ph/high'


def test_identity_rejects_empty_keys() -> None:
    with pytest.raises(ValueError, match='family_key'):
        AlarmIdentity(family_key='', alarm_key='high')


def test_planned_alarm_keeps_domain_dimensions_separate() -> None:
    value = plan(kind=AlarmKind.IMPACT, criticality=Criticality.C1, priority_order=1)
    assert value.kind is AlarmKind.IMPACT
    assert value.criticality is Criticality.C1
    assert value.priority_group == 'mill-feed'
    assert value.priority_order == 1


def test_planned_alarm_rejects_boolean_priority_order() -> None:
    with pytest.raises(TypeError, match='priority_order'):
        PlannedAlarm(
            identity=identity(),
            kind=AlarmKind.RISK,
            criticality=Criticality.C2,
            priority_group='mill-feed',
            priority_order=True,
            delivery_enabled=True,
            evaluator_key='threshold',
            alarm_configuration_revision='R1',
            tool_registry_revision='T1',
            routing=AlarmRouting(
                origin_tool_key='tool-a',
                destinations=(RoutingDestination('tool-b', 900),),
            ),
        )


def test_evaluation_context_accepts_only_static_parameter_types() -> None:
    context = EvaluationContext(
        alarm_identity=identity(),
        now=NOW,
        parameters={'mode': 'primary', 'limit': 10.0, 'enabled': True},
        data={'value': 11.0},
    )
    assert context.parameters['limit'] == 10.0
    with pytest.raises(TypeError, match='TEXT, FLOAT, or BOOLEAN'):
        EvaluationContext(
            alarm_identity=identity(),
            now=NOW,
            parameters={'count': 2},
            data=None,
        )


def test_active_evaluation_requires_evidence_and_forbids_error() -> None:
    with pytest.raises(ValueError, match='evidence_snapshot'):
        AlarmEvaluation(
            alarm_identity=identity(),
            status=AlarmStatus.ACTIVE,
            evaluated_at=NOW,
        )


def test_error_evaluation_requires_error_and_forbids_physical_evidence() -> None:
    with pytest.raises(ValueError, match='requires error'):
        AlarmEvaluation(
            alarm_identity=identity(),
            status=AlarmStatus.ERROR,
            evaluated_at=NOW,
        )
    with pytest.raises(ValueError, match='must not contain physical evidence_snapshot'):
        AlarmEvaluation(
            alarm_identity=identity(),
            status=AlarmStatus.ERROR,
            evaluated_at=NOW,
            evidence_snapshot=EvidenceSnapshot('threshold', 'v1', {'value': 1}),
            error=EvaluationError(
                origin=EvaluationErrorOrigin.QUALITY,
                error_key='stale',
                message='Input is stale',
            ),
        )


def test_affected_input_issue_allows_unknown_causality() -> None:
    error = EvaluationError(
        origin=EvaluationErrorOrigin.EVALUATOR,
        error_key='calculation_failed',
        message='Calculation failed',
        affected_inputs=(),
    )
    assert error.affected_inputs == ()
    issue = AffectedInputIssue(
        source_key='pi',
        scope_key='latest',
        reason_key='insufficient_data',
        fields=('tag_a',),
    )
    assert issue.source_key == 'pi'


def test_occurrence_open_and_closed_contracts_are_distinct() -> None:
    occurrence = AlarmOccurrence(
        occurrence_id='O1',
        alarm_identity=identity(),
        episode_id='E1',
        started_at=NOW,
        alarm_configuration_revision='R1',
        tool_registry_revision='T1',
    )
    assert occurrence.is_open
    closed = occurrence.close(
        ended_at=NOW + timedelta(minutes=1),
        reason=OccurrenceClosureReason.CONDITION_NORMALIZED,
    )
    assert not closed.is_open
    assert closed.closure_reason is OccurrenceClosureReason.CONDITION_NORMALIZED
    with pytest.raises(Exception, match='immutable'):
        closed.close(
            ended_at=NOW + timedelta(minutes=2),
            reason=OccurrenceClosureReason.CONDITION_NORMALIZED,
        )


def test_episode_closed_contract_requires_reason() -> None:
    with pytest.raises(ValueError, match='requires closure_reason'):
        AlarmEpisode(
            episode_id='E1',
            priority_group='mill-feed',
            started_at=NOW,
            ended_at=NOW + timedelta(minutes=1),
        )


def test_runtime_state_requires_error_evaluation_for_technical_hold() -> None:
    occurrence = AlarmOccurrence(
        occurrence_id='O1',
        alarm_identity=identity(),
        episode_id='E1',
        started_at=NOW,
        alarm_configuration_revision='R1',
        tool_registry_revision='T1',
    )
    with pytest.raises(ValueError, match='last_evaluation ERROR'):
        AlarmRuntimeState(
            alarm_identity=identity(),
            occurrence=occurrence,
            last_evaluation=physical('risk', AlarmStatus.ACTIVE),
            management_cycle=1,
            technical_hold=TechnicalHold(
                started_at=NOW,
                due_at=NOW + timedelta(minutes=5),
            ),
        )


def test_group_state_requires_episode_for_open_occurrence() -> None:
    occurrence = AlarmOccurrence(
        occurrence_id='O1',
        alarm_identity=identity(),
        episode_id='E1',
        started_at=NOW,
        alarm_configuration_revision='R1',
        tool_registry_revision='T1',
    )
    with pytest.raises(ValueError, match='open occurrence requires an open episode'):
        GroupLifecycleState(
            priority_group='mill-feed',
            alarms=(
                AlarmRuntimeState(
                    alarm_identity=identity(),
                    occurrence=occurrence,
                    last_evaluation=physical('risk', AlarmStatus.ACTIVE),
                    management_cycle=1,
                ),
            ),
        )


def test_domain_times_must_be_utc() -> None:
    naive = datetime(2026, 8, 24, 14, 0)
    with pytest.raises(ValueError, match='UTC'):
        EvaluationContext(alarm_identity=identity(), now=naive, parameters={}, data=None)
    assert NOW.tzinfo is UTC


def test_closed_episode_reason_values_match_contract() -> None:
    episode = AlarmEpisode(
        episode_id='E1',
        priority_group='mill-feed',
        started_at=NOW,
    ).close(
        ended_at=NOW + timedelta(minutes=1),
        reason=EpisodeClosureReason.TECHNICAL_UNCERTAINTY,
    )
    assert episode.closure_reason.value == 'technical_uncertainty'


def test_active_evaluation_requires_evidence_snapshot_contract_type() -> None:
    with pytest.raises(TypeError, match='evidence_snapshot must be an EvidenceSnapshot'):
        AlarmEvaluation(
            alarm_identity=identity(),
            status=AlarmStatus.ACTIVE,
            evaluated_at=NOW,
            evidence_snapshot=object(),
        )


def test_error_evaluation_requires_structured_error_contract_type() -> None:
    with pytest.raises(TypeError, match='error must be an EvaluationError'):
        AlarmEvaluation(
            alarm_identity=identity(),
            status=AlarmStatus.ERROR,
            evaluated_at=NOW,
            error=object(),
        )


def test_open_runtime_state_requires_non_inactive_last_evaluation() -> None:
    occurrence = AlarmOccurrence(
        occurrence_id='O1',
        alarm_identity=identity(),
        episode_id='E1',
        started_at=NOW,
        alarm_configuration_revision='R1',
        tool_registry_revision='T1',
    )
    with pytest.raises(ValueError, match='requires last_evaluation'):
        AlarmRuntimeState(alarm_identity=identity(), occurrence=occurrence)
    with pytest.raises(ValueError, match='must not retain last_evaluation INACTIVE'):
        AlarmRuntimeState(
            alarm_identity=identity(),
            occurrence=occurrence,
            last_evaluation=physical('risk', AlarmStatus.INACTIVE),
            management_cycle=1,
        )


def test_last_evaluation_does_not_persist_without_open_occurrence() -> None:
    with pytest.raises(ValueError, match='last_evaluation requires an open occurrence'):
        AlarmRuntimeState(
            alarm_identity=identity(),
            last_evaluation=physical('risk', AlarmStatus.ACTIVE),
        )


def test_configuration_closure_requires_configuration_reason_enum() -> None:
    with pytest.raises(TypeError, match='reason must be an OccurrenceClosureReason'):
        ConfigurationClosure(
            alarm_identity=identity(),
            reason='configuration_disabled',
            effective_at=NOW,
        )


def test_started_technical_hold_change_uses_hold_start_as_effective_time() -> None:
    hold = TechnicalHold(started_at=NOW, due_at=NOW + timedelta(minutes=5))
    with pytest.raises(ValueError, match='effective_at must match started_at'):
        TechnicalHoldChange(
            kind=TechnicalHoldChangeKind.STARTED,
            alarm_identity=identity(),
            occurrence_id='O1',
            effective_at=NOW + timedelta(seconds=1),
            technical_hold=hold,
        )


def test_group_lifecycle_decision_validates_all_change_collections() -> None:
    with pytest.raises(TypeError, match='occurrence_changes must contain OccurrenceChange values'):
        GroupLifecycleDecision(
            state=GroupLifecycleState(priority_group='mill-feed'),
            occurrence_changes=(object(),),
        )
    with pytest.raises(
        TypeError,
        match='technical_hold_changes must contain TechnicalHoldChange values',
    ):
        GroupLifecycleDecision(
            state=GroupLifecycleState(priority_group='mill-feed'),
            technical_hold_changes=(object(),),
        )


def test_routing_contract_matches_criticality_semantics() -> None:
    c1 = plan(
        'impact',
        kind=AlarmKind.IMPACT,
        criticality=Criticality.C1,
        priority_order=1,
        destinations=(RoutingDestination('tool-b'),),
    )
    assert c1.routing.origin_tool_key == 'tool-a'
    c2 = plan(destinations=(RoutingDestination('tool-b', 300),))
    assert c2.routing.destinations[0].delay_seconds == 300
    c3 = plan(criticality=Criticality.C3)
    assert c3.routing.destinations == ()


def test_routing_contract_rejects_ambiguous_criticality_shapes() -> None:
    with pytest.raises(ValueError, match='C1 routing destinations must be immediate'):
        plan(
            'impact',
            kind=AlarmKind.IMPACT,
            criticality=Criticality.C1,
            priority_order=1,
            destinations=(RoutingDestination('tool-b', 10),),
        )
    with pytest.raises(ValueError, match='C2 routing destinations require delay_seconds'):
        plan(destinations=(RoutingDestination('tool-b'),))
    with pytest.raises(ValueError, match='C3 routing must contain only the origin Tool'):
        plan(
            criticality=Criticality.C3,
            destinations=(RoutingDestination('tool-b'),),
        )


def test_routing_rejects_duplicate_tools() -> None:
    with pytest.raises(ValueError, match='routing tools must not contain duplicates'):
        AlarmRouting(
            origin_tool_key='tool-a',
            destinations=(RoutingDestination('tool-a', 300),),
        )


def test_runtime_assignments_are_decision_complete_and_mutually_exclusive() -> None:
    occurrence = AlarmOccurrence(
        occurrence_id='O1',
        alarm_identity=identity(),
        episode_id='E1',
        started_at=NOW,
        alarm_configuration_revision='R1',
        tool_registry_revision='T1',
    )
    state = AlarmRuntimeState(
        alarm_identity=identity(),
        occurrence=occurrence,
        last_evaluation=physical('risk', AlarmStatus.ACTIVE),
        management_cycle=1,
        assignments=(ToolAssignment('tool-a', NOW),),
        pending_assignments=(PendingToolAssignment('tool-b', NOW + timedelta(minutes=15)),),
    )
    assert state.assignments[0].tool_key == 'tool-a'
    with pytest.raises(ValueError, match='both assigned and pending'):
        AlarmRuntimeState(
            alarm_identity=identity(),
            occurrence=occurrence,
            last_evaluation=physical('risk', AlarmStatus.ACTIVE),
            management_cycle=1,
            assignments=(ToolAssignment('tool-a', NOW),),
            pending_assignments=(PendingToolAssignment('tool-a', NOW + timedelta(minutes=15)),),
        )


def test_assignments_cannot_survive_without_open_occurrence() -> None:
    with pytest.raises(ValueError, match='assignments require an open occurrence'):
        AlarmRuntimeState(
            alarm_identity=identity(),
            assignments=(ToolAssignment('tool-a', NOW),),
        )
