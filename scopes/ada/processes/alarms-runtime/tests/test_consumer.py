from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from ada.alarms.core import (
    AlarmEvaluation,
    AlarmStatus,
    DeactivationDecision,
    DeactivationDecisionKind,
    DeactivationIntent,
    DeactivationPolicy,
    EvidenceContractRef,
    EvidenceSnapshot,
    ManagementAction,
)
from ada.data.sources import DataSourceRegistry, LoadedDataSources
from ada.processes.alarms_runtime import (
    AlarmConfigurationAdoptionExecutor,
    AlarmConfigurationRevision,
    AlarmDurableInputConsumer,
    AlarmDurableInputConsumerError,
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    AlarmExecutionIteration,
    AlarmInputCursor,
    AlarmInputLocator,
    AlarmInputRecord,
    AlarmInputStream,
    AlarmOperationalCycle,
    build_alarm_execution_session,
    build_alarm_runtime_composition,
    plan_configuration_adoption,
)
from atlanticus.state import AtomicJsonStore, StateWriteError
from tests.support import NOW, build_context, identity, plan


class InputSource:
    def __init__(self) -> None:
        self.records = {
            AlarmInputStream.MANAGEMENT: [],
            AlarmInputStream.DEACTIVATION_DECISION: [],
        }

    def append(self, stream: AlarmInputStream, record: AlarmInputRecord) -> None:
        self.records[stream].append(record)

    def read_after(self, *, stream: AlarmInputStream, cursor: AlarmInputCursor | None):
        records = self.records[stream]
        if cursor is None:
            return tuple(records)
        for index, record in enumerate(records):
            if record.next_cursor == cursor:
                return tuple(records[index + 1 :])
        return tuple(
            record for record in records if _cursor_key(record.next_cursor) > _cursor_key(cursor)
        )

    def read_at(self, *, stream: AlarmInputStream, locator: AlarmInputLocator):
        for record in self.records[stream]:
            if record.locator == locator:
                return record
        raise LookupError(locator.input_id)


class CommitClock:
    def committed_at(self, *, cycle_at):
        return cycle_at + timedelta(seconds=1)


class FailingStateStore:
    def __init__(self, delegate: AtomicJsonStore) -> None:
        self.delegate = delegate

    def read(self, relative_path):
        return self.delegate.read(relative_path)

    def replace(self, relative_path, value):
        raise StateWriteError('forced consumer-state failure')


def _cursor_key(cursor: AlarmInputCursor) -> tuple[str, int]:
    return cursor.hour_bucket, cursor.byte_offset


def _record(value, *, offset: int) -> AlarmInputRecord:
    input_id = value.input_id if isinstance(value, ManagementAction) else value.decision_id
    return AlarmInputRecord(
        locator=AlarmInputLocator(
            input_id=input_id,
            hour_bucket='2026-08-24T14Z',
            byte_offset=offset,
            byte_length=100,
        ),
        next_cursor=AlarmInputCursor(
            hour_bucket='2026-08-24T14Z',
            byte_offset=offset + 100,
        ),
        value=value,
    )


def _management(*, at, until=None) -> ManagementAction:
    return ManagementAction(
        input_id='M100',
        alarm_identity=identity('risk'),
        source_occurrence_id='O1',
        tool_key='io',
        actor_key='operator',
        source_created_at=at,
        deactivation_intent=None if until is None else DeactivationIntent(effective_until=until),
    )


def _decision(*, at, kind=DeactivationDecisionKind.APPROVED) -> DeactivationDecision:
    return DeactivationDecision(
        decision_id='D100',
        request_id='DR-M100',
        kind=kind,
        decided_at=at,
        actor_key='approver',
    )


def _runtime(tmp_path: Path, *, approval_required: bool = True):
    planned = replace(
        plan('risk'),
        evaluator_key='risk-evaluator',
        deactivation_policy=DeactivationPolicy(approval_required=approval_required),
    )
    contract = AlarmEvaluatorContract(
        family_key='mill',
        evaluator_key='risk-evaluator',
        evaluator=lambda context: AlarmEvaluation(
            alarm_identity=identity('risk'),
            status=AlarmStatus.ACTIVE,
            evaluated_at=context.now,
            evidence_snapshot=EvidenceSnapshot(
                contract_key='threshold',
                contract_version='v1',
                payload={'status': 'ACTIVE'},
            ),
        ),
    )
    session = build_alarm_execution_session(
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        planned_alarms=(planned,),
        evaluator_registry=AlarmEvaluatorRegistry((contract,)),
    )
    context = build_context(tmp_path)
    composition = build_alarm_runtime_composition(runtime_configuration=context.configuration)
    occurrence_counter = {'value': 0}
    episode_counter = {'value': 0}

    def occurrence_id(_identity, _at):
        occurrence_counter['value'] += 1
        return f'O{occurrence_counter["value"]}'

    def episode_id(_priority_group, _at):
        episode_counter['value'] += 1
        return f'E{episode_counter["value"]}'

    def reappearance_due_at(action):
        return action.source_created_at + timedelta(hours=10)

    cycle = AlarmOperationalCycle(
        session=session,
        composition=composition,
        occurrence_id_factory=occurrence_id,
        episode_id_factory=episode_id,
        commit_time_provider=CommitClock(),
        runtime_artifact_version='ada-alarms-runtime/0.6.0',
        technical_evidence_contract=EvidenceContractRef(
            contract_key='evaluation-error',
            contract_version='v1',
        ),
        management_effect_id_factory=lambda action: f'ME-{action.input_id}',
        reappearance_due_at_resolver=reappearance_due_at,
        deactivation_request_id_factory=lambda action: f'DR-{action.input_id}',
        deactivation_effect_id_factory=lambda request: f'DE-{request.request_id}',
    )
    cycle.execute(context, _iteration(session, NOW))
    return session, context, composition, cycle


def _iteration(session, at):
    return AlarmExecutionIteration(
        session=session,
        loaded_sources=LoadedDataSources(
            as_of=at,
            plan=session.data_plan,
            registry=DataSourceRegistry({}),
            loaded={},
            failures={},
        ),
    )


def _state_document(composition):
    store = AtomicJsonStore(root_path=composition.durability.persistence.paths.alarms_root)
    return store.read('runtime/state/consumers/management.json')


def _durable_records(composition):
    return tuple(
        entry.record for entry in composition.durability.persistence.read_durable_records()
    )


def test_management_receipt_becomes_durable_before_consumer_cursor_advances(tmp_path: Path) -> None:
    session, context, composition, cycle = _runtime(tmp_path, approval_required=False)
    source = InputSource()
    at = NOW + timedelta(minutes=1)
    source.append(AlarmInputStream.MANAGEMENT, _record(_management(at=at), offset=0))
    consumer = AlarmDurableInputConsumer(composition=composition, source=source)

    result = consumer.execute(context, cycle=cycle, iteration=_iteration(session, at))
    state = _state_document(composition)

    assert result.commit_result is not None
    assert state['management']['cursor'] == {
        'hour_bucket': '2026-08-24T14Z',
        'byte_offset': 100,
    }
    assert state['management']['pending'] == []
    receipts = [
        receipt
        for record in _durable_records(composition)
        for receipt in record.records.get('input_receipts', [])
    ]
    assert any(receipt['input_id'] == 'M100' for receipt in receipts)


def test_crash_after_materialization_redelivery_does_not_reapply_management(tmp_path: Path) -> None:
    session, context, composition, cycle = _runtime(tmp_path)
    source = InputSource()
    at = NOW + timedelta(minutes=1)
    until = at + timedelta(hours=7)
    source.append(
        AlarmInputStream.MANAGEMENT,
        _record(_management(at=at, until=until), offset=0),
    )
    consumer = AlarmDurableInputConsumer(composition=composition, source=source)
    consumer._state_store = FailingStateStore(consumer._state_store)

    with pytest.raises(AlarmDurableInputConsumerError, match='could not persist'):
        consumer.execute(context, cycle=cycle, iteration=_iteration(session, at))

    records_after_crash = _durable_records(composition)
    retry = AlarmDurableInputConsumer(composition=composition, source=source)
    retry.execute(
        context,
        cycle=cycle,
        iteration=_iteration(session, at + timedelta(minutes=1)),
    )
    records_after_retry = _durable_records(composition)
    state = _state_document(composition)

    receipts = [
        receipt
        for record in records_after_retry
        for receipt in record.records.get('input_receipts', [])
        if receipt['input_id'] == 'M100'
    ]
    requests = [
        request
        for record in records_after_retry
        for request in record.records.get('deactivation_requests', [])
        if request['request_id'] == 'DR-M100'
    ]
    assert len(receipts) == 1
    assert len(requests) == 1
    assert len(records_after_retry) == len(records_after_crash)
    assert state['pending_deactivation_request_ids'] == ['DR-M100']
    assert state['management']['cursor']['byte_offset'] == 100


def test_early_decision_advances_its_cursor_and_is_kept_pending(tmp_path: Path) -> None:
    session, context, composition, cycle = _runtime(tmp_path)
    source = InputSource()
    decision_at = NOW + timedelta(hours=2)
    source.append(
        AlarmInputStream.DEACTIVATION_DECISION,
        _record(_decision(at=decision_at), offset=0),
    )
    consumer = AlarmDurableInputConsumer(composition=composition, source=source)

    consumer.execute(context, cycle=cycle, iteration=_iteration(session, decision_at))
    state = _state_document(composition)

    assert state['decisions']['cursor']['byte_offset'] == 100
    assert state['decisions']['pending'] == [
        {
            'input_id': 'D100',
            'hour_bucket': '2026-08-24T14Z',
            'byte_offset': 0,
            'byte_length': 100,
        }
    ]
    receipts = [
        receipt
        for record in _durable_records(composition)
        for receipt in record.records.get('input_receipts', [])
    ]
    assert not any(receipt['input_id'] == 'D100' for receipt in receipts)


def test_early_decision_is_applied_only_after_request_is_durable_and_keeps_original_window(
    tmp_path: Path,
) -> None:
    session, context, composition, cycle = _runtime(tmp_path)
    source = InputSource()
    requested_at = NOW + timedelta(minutes=1)
    effective_until = requested_at + timedelta(hours=7)
    decision_at = requested_at + timedelta(hours=2)
    source.append(
        AlarmInputStream.DEACTIVATION_DECISION,
        _record(_decision(at=decision_at), offset=0),
    )
    consumer = AlarmDurableInputConsumer(composition=composition, source=source)
    consumer.execute(context, cycle=cycle, iteration=_iteration(session, decision_at))
    source.append(
        AlarmInputStream.MANAGEMENT,
        _record(_management(at=requested_at, until=effective_until), offset=0),
    )

    consumer.execute(
        context,
        cycle=cycle,
        iteration=_iteration(session, decision_at + timedelta(minutes=1)),
    )
    state_after_request = _state_document(composition)
    receipts_after_request = [
        receipt
        for record in _durable_records(composition)
        for receipt in record.records.get('input_receipts', [])
    ]

    assert state_after_request['pending_deactivation_request_ids'] == ['DR-M100']
    assert state_after_request['decisions']['pending'][0]['input_id'] == 'D100'
    assert not any(receipt['input_id'] == 'D100' for receipt in receipts_after_request)

    consumer.execute(
        context,
        cycle=cycle,
        iteration=_iteration(session, decision_at + timedelta(minutes=2)),
    )
    state_after_decision = _state_document(composition)
    records = _durable_records(composition)
    decision_receipts = [
        receipt
        for record in records
        for receipt in record.records.get('input_receipts', [])
        if receipt['input_id'] == 'D100'
    ]
    effects = [
        effect for record in records for effect in record.records.get('deactivation_effects', [])
    ]

    assert len(decision_receipts) == 1
    assert state_after_decision['pending_deactivation_request_ids'] == []
    assert state_after_decision['decisions']['pending'] == []
    assert effects[-1]['effective_from'] == decision_at.isoformat().replace('+00:00', 'Z')
    assert effects[-1]['effective_until'] == effective_until.isoformat().replace('+00:00', 'Z')


def test_missing_consumer_state_after_durable_decision_fails_closed(tmp_path: Path) -> None:
    session, context, composition, cycle = _runtime(tmp_path)
    source = InputSource()
    requested_at = NOW + timedelta(minutes=1)
    decision_at = requested_at + timedelta(minutes=1)
    source.append(
        AlarmInputStream.MANAGEMENT,
        _record(_management(at=requested_at, until=requested_at + timedelta(hours=1)), offset=0),
    )
    consumer = AlarmDurableInputConsumer(composition=composition, source=source)
    consumer.execute(context, cycle=cycle, iteration=_iteration(session, requested_at))
    source.append(
        AlarmInputStream.DEACTIVATION_DECISION,
        _record(_decision(at=decision_at, kind=DeactivationDecisionKind.REJECTED), offset=0),
    )
    consumer.execute(context, cycle=cycle, iteration=_iteration(session, decision_at))
    state_path = (
        composition.durability.persistence.paths.alarms_root
        / 'runtime/state/consumers/management.json'
    )
    state_path.unlink()

    rebuilt = AlarmDurableInputConsumer(composition=composition, source=source)
    with pytest.raises(AlarmDurableInputConsumerError, match='state is missing'):
        rebuilt.execute(
            context,
            cycle=cycle,
            iteration=_iteration(session, decision_at + timedelta(minutes=1)),
        )


def test_late_decision_after_configuration_disable_uses_durable_request_routing(
    tmp_path: Path,
) -> None:
    session, context, composition, cycle = _runtime(tmp_path)
    source = InputSource()
    requested_at = NOW + timedelta(minutes=1)
    source.append(
        AlarmInputStream.MANAGEMENT,
        _record(
            _management(at=requested_at, until=requested_at + timedelta(hours=7)),
            offset=0,
        ),
    )
    consumer = AlarmDurableInputConsumer(composition=composition, source=source)
    consumer.execute(context, cycle=cycle, iteration=_iteration(session, requested_at))

    source_revision = AlarmConfigurationRevision(
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        defined_alarm_identities=session.identities,
        session=session,
    )
    target_session = build_alarm_execution_session(
        alarm_configuration_revision='R43',
        tool_registry_revision='T18',
        planned_alarms=(),
        evaluator_registry=AlarmEvaluatorRegistry(()),
    )
    target_revision = AlarmConfigurationRevision(
        alarm_configuration_revision='R43',
        tool_registry_revision='T18',
        defined_alarm_identities=session.identities,
        session=target_session,
    )
    adoption = plan_configuration_adoption(source_revision, target_revision)
    executor = AlarmConfigurationAdoptionExecutor(
        composition=composition,
        commit_time_provider=CommitClock(),
        runtime_artifact_version='ada-alarms-runtime/0.9.0',
    )
    executor.execute(
        context,
        adoption,
        effective_at=requested_at + timedelta(minutes=1),
    )

    target_cycle = AlarmOperationalCycle(
        session=target_session,
        composition=composition,
        occurrence_id_factory=lambda _identity, _at: 'UNUSED-O',
        episode_id_factory=lambda _priority_group, _at: 'UNUSED-E',
        commit_time_provider=CommitClock(),
        runtime_artifact_version='ada-alarms-runtime/0.9.0',
        technical_evidence_contract=EvidenceContractRef(
            contract_key='evaluation-error',
            contract_version='v1',
        ),
        management_effect_id_factory=lambda action: f'ME-{action.input_id}',
        reappearance_due_at_resolver=lambda action: action.source_created_at + timedelta(hours=10),
        deactivation_request_id_factory=lambda action: f'DR-{action.input_id}',
        deactivation_effect_id_factory=lambda request: f'DE-{request.request_id}',
    )
    decision_at = requested_at + timedelta(minutes=2)
    source.append(
        AlarmInputStream.DEACTIVATION_DECISION,
        _record(_decision(at=decision_at), offset=0),
    )

    consumer.execute(
        context,
        cycle=target_cycle,
        iteration=_iteration(target_session, decision_at),
    )

    state = _state_document(composition)
    receipts = [
        receipt
        for record in _durable_records(composition)
        for receipt in record.records.get('input_receipts', [])
        if receipt['input_id'] == 'D100'
    ]
    assert len(receipts) == 1
    assert receipts[0]['outcome'] == 'STALE_TARGET'
    assert state['pending_deactivation_request_ids'] == []
    assert state['decisions']['pending'] == []
