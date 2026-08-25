# Consume Management y Deactivation Decisions con cursores/pending durables y reconstruye requests desde WAL sin depender de la sesión vigente.
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ada.alarms.core import (
    AlarmIdentity,
    DeactivationDecision,
    DeactivationRequest,
    InputKind,
    ManagementAction,
)
from ada.alarms.persistence import JournalPosition
from ada.processes.alarms_runtime.composition import AlarmRuntimeComposition
from ada.processes.alarms_runtime.cycle import AlarmOperationalCycle, AlarmOperationalCycleResult
from ada.processes.alarms_runtime.inputs import (
    AlarmInputCursor,
    AlarmInputLocator,
    AlarmInputRecord,
    AlarmInputSource,
    AlarmInputStream,
    AlarmOperationalInputs,
    AlarmPendingDeactivationRequest,
)
from ada.processes.alarms_runtime.iteration import AlarmExecutionIteration
from atlanticus.runtime import JobRuntimeContext
from atlanticus.state import AtomicJsonStore, StateError

_CONSUMER_STATE_SCHEMA_VERSION = 'alarm-runtime-input-consumer-state.v1'
_CONSUMER_STATE_PATH = 'runtime/state/consumers/management.json'


# Contrato AlarmDurableInputConsumerError: agrupa datos y valida invariantes cerca de su frontera.
class AlarmDurableInputConsumerError(ValueError):
    pass


# Contrato _StreamState: agrupa datos y valida invariantes cerca de su frontera.
@dataclass(frozen=True, slots=True)
class _StreamState:
    cursor: AlarmInputCursor | None = None
    pending: tuple[AlarmInputLocator, ...] = ()


# Contrato _ConsumerState: agrupa datos y valida invariantes cerca de su frontera.
@dataclass(frozen=True, slots=True)
class _ConsumerState:
    management: _StreamState = _StreamState()
    decisions: _StreamState = _StreamState()
    pending_deactivation_request_ids: tuple[str, ...] = ()


# Contrato _DurableIndex: agrupa datos y valida invariantes cerca de su frontera.
@dataclass(slots=True)
class _DurableIndex:
    position: JournalPosition | None = None
    receipts: dict[str, Mapping[str, Any]] | None = None
    requests: dict[str, Mapping[str, Any]] | None = None
    requests_by_management_input: dict[str, str] | None = None
    request_priority_groups: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.receipts is None:
            self.receipts = {}
        if self.requests is None:
            self.requests = {}
        if self.requests_by_management_input is None:
            self.requests_by_management_input = {}
        if self.request_priority_groups is None:
            self.request_priority_groups = {}


# Contrato AlarmDurableInputConsumer: agrupa datos y valida invariantes cerca de su frontera.
@dataclass(slots=True)
class AlarmDurableInputConsumer:
    composition: AlarmRuntimeComposition
    source: AlarmInputSource
    _state_store: AtomicJsonStore = field(init=False, repr=False)
    _index: _DurableIndex = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.composition, AlarmRuntimeComposition):
            raise TypeError('composition must be AlarmRuntimeComposition')
        if not isinstance(self.source, AlarmInputSource):
            raise TypeError('source must implement AlarmInputSource')
        self._state_store = AtomicJsonStore(
            root_path=self.composition.durability.persistence.paths.alarms_root
        )
        self._index = _DurableIndex()

    def execute(
        self,
        context: JobRuntimeContext,
        *,
        cycle: AlarmOperationalCycle,
        iteration: AlarmExecutionIteration,
    ) -> AlarmOperationalCycleResult:
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be JobRuntimeContext')
        if not isinstance(cycle, AlarmOperationalCycle):
            raise TypeError('cycle must be AlarmOperationalCycle')
        if not isinstance(iteration, AlarmExecutionIteration):
            raise TypeError('iteration must be AlarmExecutionIteration')
        if cycle.composition is not self.composition:
            raise AlarmDurableInputConsumerError('cycle must use the consumer runtime composition')
        self._require_materialized_head()
        self._refresh_index()
        state = self._read_state()
        if state is None:
            state = self._bootstrap_state()
        identity_by_key = {
            entry.identity.canonical_key: entry.identity for entry in iteration.session.entries
        }
        pending_requests = self._resolve_pending_requests(
            state.pending_deactivation_request_ids,
            identity_by_key=identity_by_key,
        )
        management_pending = self._read_pending(
            AlarmInputStream.MANAGEMENT,
            state.management.pending,
        )
        decision_pending = self._read_pending(
            AlarmInputStream.DEACTIVATION_DECISION,
            state.decisions.pending,
        )
        management_fresh = self._read_fresh(
            AlarmInputStream.MANAGEMENT,
            state.management.cursor,
        )
        decision_fresh = self._read_fresh(
            AlarmInputStream.DEACTIVATION_DECISION,
            state.decisions.cursor,
        )
        management_records = _merge_records(management_pending, management_fresh)
        decision_records = _merge_records(decision_pending, decision_fresh)
        management_actions = tuple(
            record.value
            for record in management_records
            if isinstance(record.value, ManagementAction)
            and not self._management_processed(record.value.input_id)
        )
        durable_request_ids = set(self._index.requests)
        decisions = tuple(
            record.value
            for record in decision_records
            if isinstance(record.value, DeactivationDecision)
            and not self._decision_processed(record.value.decision_id)
            and record.value.request_id in durable_request_ids
        )
        operational_inputs = AlarmOperationalInputs(
            management_actions=management_actions,
            pending_deactivation_requests=pending_requests,
            deactivation_decisions=decisions,
        )
        result = cycle.execute(context, iteration, operational_inputs=operational_inputs)
        self._refresh_index()
        self._verify_management_terminal(management_actions)
        next_state = self._next_state(
            state,
            management_fresh=management_fresh,
            decision_records=decision_records,
            decision_fresh=decision_fresh,
        )
        self._replace_state(context, next_state)
        return result

    def _require_materialized_head(self) -> None:
        head = self.composition.durability.persistence.read_head()
        if not head.aligned:
            raise AlarmDurableInputConsumerError(
                'Alarm Engine journal must be recovered before consuming durable inputs'
            )

    def _refresh_index(self) -> None:
        entries = self.composition.durability.persistence.read_durable_records(
            after=self._index.position
        )
        for entry in entries:
            records = entry.record.records
            for receipt in records.get('input_receipts', []):
                receipt_id = f'{receipt["input_kind"]}:{receipt["input_id"]}'
                existing = self._index.receipts.get(receipt_id)
                if existing is not None and dict(existing) != dict(receipt):
                    raise AlarmDurableInputConsumerError(
                        f'durable input receipt is inconsistent: {receipt_id}'
                    )
                self._index.receipts[receipt_id] = receipt
            for request in records.get('deactivation_requests', []):
                request_id = request['request_id']
                existing = self._index.requests.get(request_id)
                if existing is not None and dict(existing) != dict(request):
                    raise AlarmDurableInputConsumerError(
                        f'durable deactivation request is inconsistent: {request_id}'
                    )
                management_input_id = request['source_management_input_id']
                existing_request_id = self._index.requests_by_management_input.get(
                    management_input_id
                )
                if existing_request_id is not None and existing_request_id != request_id:
                    raise AlarmDurableInputConsumerError(
                        'management input maps to multiple durable deactivation requests'
                    )
                self._index.requests[request_id] = request
                self._index.requests_by_management_input[management_input_id] = request_id
                priority_group = entry.record.commit.priority_group
                existing_priority_group = self._index.request_priority_groups.get(request_id)
                if (
                    existing_priority_group is not None
                    and existing_priority_group != priority_group
                ):
                    raise AlarmDurableInputConsumerError(
                        f'durable deactivation request priority_group is inconsistent: {request_id}'
                    )
                self._index.request_priority_groups[request_id] = priority_group
            self._index.position = entry.end

    def _read_state(self) -> _ConsumerState | None:
        try:
            document = self._state_store.read(_CONSUMER_STATE_PATH)
        except StateError as error:
            raise AlarmDurableInputConsumerError('could not read alarm consumer state') from error
        if document is None:
            return None
        try:
            return _decode_consumer_state(document)
        except (TypeError, ValueError, KeyError) as error:
            raise AlarmDurableInputConsumerError('alarm consumer state is invalid') from error

    def _bootstrap_state(self) -> _ConsumerState:
        if any(
            receipt_id.startswith(f'{InputKind.DEACTIVATION_DECISION.value}:')
            for receipt_id in self._index.receipts
        ):
            raise AlarmDurableInputConsumerError(
                'alarm consumer state is missing after durable deactivation decisions exist'
            )
        pending = tuple(
            sorted(
                request_id
                for request_id, request in self._index.requests.items()
                if request['approval_required']
            )
        )
        return _ConsumerState(pending_deactivation_request_ids=pending)

    def _resolve_pending_requests(
        self,
        request_ids: Sequence[str],
        *,
        identity_by_key: Mapping[str, AlarmIdentity],
    ) -> tuple[AlarmPendingDeactivationRequest, ...]:
        requests = []
        for request_id in request_ids:
            document = self._index.requests.get(request_id)
            if document is None:
                raise AlarmDurableInputConsumerError(
                    f'pending deactivation request is not durable: {request_id}'
                )
            identity = identity_by_key.get(document['alarm_key'])
            if identity is None:
                identity = _identity_from_canonical_key(document['alarm_key'])
            priority_group = self._index.request_priority_groups.get(request_id)
            if priority_group is None:
                raise AlarmDurableInputConsumerError(
                    f'durable deactivation request priority_group is missing: {request_id}'
                )
            requests.append(
                AlarmPendingDeactivationRequest(
                    priority_group=priority_group,
                    request=DeactivationRequest(
                        request_id=request_id,
                        alarm_identity=identity,
                        source_management_input_id=document['source_management_input_id'],
                        source_occurrence_id=document['source_occurrence_id'],
                        requested_at=_parse_utc(document['requested_at']),
                        effective_until=_parse_utc(document['effective_until']),
                        approval_required=document['approval_required'],
                    ),
                )
            )
        return tuple(requests)

    def _read_pending(
        self,
        stream: AlarmInputStream,
        locators: Sequence[AlarmInputLocator],
    ) -> tuple[AlarmInputRecord, ...]:
        records = []
        for locator in locators:
            record = self.source.read_at(stream=stream, locator=locator)
            _validate_source_record(stream, record)
            if record.locator != locator:
                raise AlarmDurableInputConsumerError('input source changed a pending locator')
            records.append(record)
        return tuple(records)

    def _read_fresh(
        self,
        stream: AlarmInputStream,
        cursor: AlarmInputCursor | None,
    ) -> tuple[AlarmInputRecord, ...]:
        records = self.source.read_after(stream=stream, cursor=cursor)
        if isinstance(records, str | bytes) or not isinstance(records, Sequence):
            raise AlarmDurableInputConsumerError('input source read_after must return a sequence')
        normalized = tuple(records)
        for record in normalized:
            _validate_source_record(stream, record)
        _validate_fresh_order(normalized, cursor=cursor)
        return normalized

    def _management_processed(self, input_id: str) -> bool:
        keys = (
            f'{InputKind.MANAGEMENT.value}:{input_id}',
            f'{InputKind.DEACTIVATION_REQUEST.value}:{input_id}',
        )
        present = tuple(key for key in keys if key in self._index.receipts)
        if len(present) > 1:
            raise AlarmDurableInputConsumerError(
                f'management input has conflicting durable receipts: {input_id}'
            )
        return bool(present)

    def _decision_processed(self, decision_id: str) -> bool:
        return f'{InputKind.DEACTIVATION_DECISION.value}:{decision_id}' in self._index.receipts

    def _verify_management_terminal(self, actions: Sequence[ManagementAction]) -> None:
        for action in actions:
            if not self._management_processed(action.input_id):
                raise AlarmDurableInputConsumerError(
                    'management input did not produce a durable terminal receipt: '
                    f'{action.input_id}'
                )

    def _next_state(
        self,
        state: _ConsumerState,
        *,
        management_fresh: Sequence[AlarmInputRecord],
        decision_records: Sequence[AlarmInputRecord],
        decision_fresh: Sequence[AlarmInputRecord],
    ) -> _ConsumerState:
        request_ids = set(state.pending_deactivation_request_ids)
        for record in management_fresh:
            if not isinstance(record.value, ManagementAction):
                continue
            request_id = self._index.requests_by_management_input.get(record.value.input_id)
            if request_id is not None and self._index.requests[request_id]['approval_required']:
                request_ids.add(request_id)
        pending_decisions = []
        for record in decision_records:
            if not isinstance(record.value, DeactivationDecision):
                continue
            if self._decision_processed(record.value.decision_id):
                request_ids.discard(record.value.request_id)
            else:
                pending_decisions.append(record.locator)
        return _ConsumerState(
            management=_StreamState(
                cursor=_last_cursor(management_fresh, state.management.cursor),
                pending=(),
            ),
            decisions=_StreamState(
                cursor=_last_cursor(decision_fresh, state.decisions.cursor),
                pending=_unique_locators(pending_decisions),
            ),
            pending_deactivation_request_ids=tuple(sorted(request_ids)),
        )

    def _replace_state(self, context: JobRuntimeContext, state: _ConsumerState) -> None:
        document = _encode_consumer_state(state)
        context.assert_lease_current()
        try:
            with context.fenced_mutation():
                context.assert_lease_current()
                self._state_store.replace(_CONSUMER_STATE_PATH, document)
        except StateError as error:
            raise AlarmDurableInputConsumerError(
                'could not persist alarm consumer state'
            ) from error


# Auxiliar _validate_source_record: mantiene una responsabilidad interna acotada y determinista.
def _validate_source_record(stream: AlarmInputStream, record: object) -> None:
    if not isinstance(record, AlarmInputRecord):
        raise AlarmDurableInputConsumerError('input source returned an invalid record')
    if stream is AlarmInputStream.MANAGEMENT and not isinstance(record.value, ManagementAction):
        raise AlarmDurableInputConsumerError('management stream returned a non-management input')
    if stream is AlarmInputStream.DEACTIVATION_DECISION and not isinstance(
        record.value, DeactivationDecision
    ):
        raise AlarmDurableInputConsumerError('decision stream returned a non-decision input')


# Auxiliar _validate_fresh_order: mantiene una responsabilidad interna acotada y determinista.
def _validate_fresh_order(
    records: Sequence[AlarmInputRecord],
    *,
    cursor: AlarmInputCursor | None,
) -> None:
    seen: set[str] = set()
    previous = cursor
    for record in records:
        if record.locator.input_id in seen:
            raise AlarmDurableInputConsumerError('input source returned duplicate input_id values')
        seen.add(record.locator.input_id)
        if previous is not None and _cursor_key(record.next_cursor) <= _cursor_key(previous):
            raise AlarmDurableInputConsumerError('input source cursor must advance monotonically')
        previous = record.next_cursor


# Auxiliar _merge_records: mantiene una responsabilidad interna acotada y determinista.
def _merge_records(
    pending: Sequence[AlarmInputRecord], fresh: Sequence[AlarmInputRecord]
) -> tuple[AlarmInputRecord, ...]:
    merged: dict[str, AlarmInputRecord] = {}
    for record in (*pending, *fresh):
        existing = merged.get(record.locator.input_id)
        if existing is not None and existing != record:
            raise AlarmDurableInputConsumerError('input source returned inconsistent redelivery')
        merged[record.locator.input_id] = record
    return tuple(merged.values())


# Auxiliar _last_cursor: mantiene una responsabilidad interna acotada y determinista.
def _last_cursor(
    records: Sequence[AlarmInputRecord],
    previous: AlarmInputCursor | None,
) -> AlarmInputCursor | None:
    return previous if not records else records[-1].next_cursor


# Auxiliar _unique_locators: mantiene una responsabilidad interna acotada y determinista.
def _unique_locators(locators: Sequence[AlarmInputLocator]) -> tuple[AlarmInputLocator, ...]:
    unique: dict[str, AlarmInputLocator] = {}
    for locator in locators:
        existing = unique.get(locator.input_id)
        if existing is not None and existing != locator:
            raise AlarmDurableInputConsumerError(
                'pending input locator changed for the same input_id'
            )
        unique[locator.input_id] = locator
    return tuple(unique.values())


# Auxiliar _encode_consumer_state: mantiene una responsabilidad interna acotada y determinista.
def _encode_consumer_state(state: _ConsumerState) -> dict[str, Any]:
    return {
        'consumer_state_schema_version': _CONSUMER_STATE_SCHEMA_VERSION,
        'management': _encode_stream_state(state.management),
        'decisions': _encode_stream_state(state.decisions),
        'pending_deactivation_request_ids': list(state.pending_deactivation_request_ids),
    }


# Auxiliar _encode_stream_state: mantiene una responsabilidad interna acotada y determinista.
def _encode_stream_state(state: _StreamState) -> dict[str, Any]:
    return {
        'cursor': None if state.cursor is None else _encode_cursor(state.cursor),
        'pending': [_encode_locator(locator) for locator in state.pending],
    }


# Auxiliar _encode_cursor: mantiene una responsabilidad interna acotada y determinista.
def _encode_cursor(cursor: AlarmInputCursor) -> dict[str, Any]:
    return {'hour_bucket': cursor.hour_bucket, 'byte_offset': cursor.byte_offset}


# Auxiliar _encode_locator: mantiene una responsabilidad interna acotada y determinista.
def _encode_locator(locator: AlarmInputLocator) -> dict[str, Any]:
    return {
        'input_id': locator.input_id,
        'hour_bucket': locator.hour_bucket,
        'byte_offset': locator.byte_offset,
        'byte_length': locator.byte_length,
    }


# Auxiliar _decode_consumer_state: mantiene una responsabilidad interna acotada y determinista.
def _decode_consumer_state(document: Mapping[str, Any]) -> _ConsumerState:
    if set(document) != {
        'consumer_state_schema_version',
        'management',
        'decisions',
        'pending_deactivation_request_ids',
    }:
        raise ValueError('consumer state contains unsupported fields')
    if document['consumer_state_schema_version'] != _CONSUMER_STATE_SCHEMA_VERSION:
        raise ValueError('consumer state schema version is unsupported')
    request_ids = document['pending_deactivation_request_ids']
    if not isinstance(request_ids, list):
        raise TypeError('pending_deactivation_request_ids must be an array')
    normalized_request_ids = tuple(_non_empty_string(value, 'request_id') for value in request_ids)
    if len(normalized_request_ids) != len(set(normalized_request_ids)):
        raise ValueError('pending_deactivation_request_ids must not contain duplicates')
    return _ConsumerState(
        management=_decode_stream_state(document['management']),
        decisions=_decode_stream_state(document['decisions']),
        pending_deactivation_request_ids=normalized_request_ids,
    )


# Auxiliar _decode_stream_state: mantiene una responsabilidad interna acotada y determinista.
def _decode_stream_state(value: Any) -> _StreamState:
    if not isinstance(value, Mapping) or set(value) != {'cursor', 'pending'}:
        raise ValueError('consumer stream state is invalid')
    cursor_value = value['cursor']
    pending_value = value['pending']
    if cursor_value is not None and not isinstance(cursor_value, Mapping):
        raise TypeError('consumer cursor must be an object or null')
    if not isinstance(pending_value, list):
        raise TypeError('consumer pending must be an array')
    cursor = None if cursor_value is None else _decode_cursor(cursor_value)
    pending = tuple(_decode_locator(item) for item in pending_value)
    if len({item.input_id for item in pending}) != len(pending):
        raise ValueError('consumer pending must not contain duplicate input_id values')
    return _StreamState(cursor=cursor, pending=pending)


# Auxiliar _decode_cursor: mantiene una responsabilidad interna acotada y determinista.
def _decode_cursor(value: Mapping[str, Any]) -> AlarmInputCursor:
    if set(value) != {'hour_bucket', 'byte_offset'}:
        raise ValueError('consumer cursor contains unsupported fields')
    return AlarmInputCursor(hour_bucket=value['hour_bucket'], byte_offset=value['byte_offset'])


# Auxiliar _decode_locator: mantiene una responsabilidad interna acotada y determinista.
def _decode_locator(value: Any) -> AlarmInputLocator:
    if not isinstance(value, Mapping) or set(value) != {
        'input_id',
        'hour_bucket',
        'byte_offset',
        'byte_length',
    }:
        raise ValueError('consumer pending locator is invalid')
    return AlarmInputLocator(
        input_id=value['input_id'],
        hour_bucket=value['hour_bucket'],
        byte_offset=value['byte_offset'],
        byte_length=value['byte_length'],
    )


# Auxiliar _parse_utc: mantiene una responsabilidad interna acotada y determinista.
def _parse_utc(value: Any) -> datetime:
    text = _non_empty_string(value, 'timestamp')
    try:
        timestamp = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as error:
        raise AlarmDurableInputConsumerError('durable request timestamp is invalid') from error
    if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(timestamp):
        raise AlarmDurableInputConsumerError('durable request timestamp must be UTC')
    return timestamp.astimezone(UTC)


# Auxiliar _non_empty_string: mantiene una responsabilidad interna acotada y determinista.
def _non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a non-empty string')
    return value.strip()


# Auxiliar _cursor_key: mantiene una responsabilidad interna acotada y determinista.
def _cursor_key(cursor: AlarmInputCursor) -> tuple[str, int]:
    return cursor.hour_bucket, cursor.byte_offset


# Auxiliar _identity_from_canonical_key: mantiene una responsabilidad interna acotada y determinista.
def _identity_from_canonical_key(value: Any) -> AlarmIdentity:
    canonical_key = _non_empty_string(value, 'alarm_key')
    parts = canonical_key.split('/')
    if len(parts) != 2 or not all(part.strip() for part in parts):
        raise AlarmDurableInputConsumerError('durable request alarm_key is invalid')
    return AlarmIdentity(family_key=parts[0].strip(), alarm_key=parts[1].strip())
