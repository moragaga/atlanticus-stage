# Espejo pedagógico de los modelos físicos de Alarm Persistence.
# EngineCommitRecord representa una decisión completa persistible y verificable mediante hash canónico.
# GroupRuntimeSnapshot conserva el after-image operativo de un priority_group y su last_commit_id.
# JournalHead separa durable de materialized para permitir recovery determinista sin reevaluar decisiones ya confirmadas.
# Los modelos validan invariantes antes de que los datos crucen una frontera física.

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ada.alarms.persistence.errors import (
    AlarmPersistenceCorruptionError,
    AlarmPersistenceValidationError,
)
from atlanticus.json import JsonDocument, normalize_json_document

ENGINE_COMMIT_RECORD_SCHEMA_VERSION = 'engine-commit-record.v1'
GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION = 'group-runtime-snapshot.v1'
JOURNAL_HEAD_SCHEMA_VERSION = 'journal-head.v1'

_SEGMENT_ID_PATTERN = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}Z#\d{4}')
_RECORD_COLLECTIONS = {
    'assignment_changes',
    'deactivation_effects',
    'episode_changes',
    'evidence_records',
    'input_receipts',
    'journey_events',
    'management_effects',
    'occurrence_changes',
}


@dataclass(frozen=True, slots=True, order=True)
class JournalPosition:
    segment_id: str
    byte_offset: int
    commit_id: str

    def __post_init__(self) -> None:
        _require_segment_id(self.segment_id)
        if isinstance(self.byte_offset, bool) or not isinstance(self.byte_offset, int):
            raise TypeError('byte_offset must be an int')
        if self.byte_offset <= 0:
            raise ValueError('byte_offset must be greater than zero')
        _require_non_empty_string(self.commit_id, 'commit_id')

    def as_document(self) -> JsonDocument:
        return {
            'segment_id': self.segment_id,
            'byte_offset': self.byte_offset,
            'commit_id': self.commit_id,
        }

    @classmethod
    def from_document(cls, value: Mapping[str, Any]) -> JournalPosition:
        document = _require_exact_document(
            value,
            required={'segment_id', 'byte_offset', 'commit_id'},
            label='journal position',
        )
        try:
            return cls(
                segment_id=document['segment_id'],
                byte_offset=document['byte_offset'],
                commit_id=document['commit_id'],
            )
        except (TypeError, ValueError) as error:
            raise AlarmPersistenceCorruptionError('journal position is invalid') from error


@dataclass(frozen=True, slots=True)
class JournalHead:
    durable: JournalPosition | None = None
    materialized: JournalPosition | None = None

    def __post_init__(self) -> None:
        if self.durable is not None and not isinstance(self.durable, JournalPosition):
            raise TypeError('durable must be a JournalPosition')
        if self.materialized is not None and not isinstance(self.materialized, JournalPosition):
            raise TypeError('materialized must be a JournalPosition')
        if self.durable is None and self.materialized is not None:
            raise ValueError('materialized position requires a durable position')
        if self.durable is not None and self.materialized is not None:
            _validate_position_order(self.materialized, self.durable)

    @property
    def aligned(self) -> bool:
        return self.durable == self.materialized

    def as_document(self) -> JsonDocument:
        return {
            'journal_head_schema_version': JOURNAL_HEAD_SCHEMA_VERSION,
            'durable': None if self.durable is None else self.durable.as_document(),
            'materialized': (
                None if self.materialized is None else self.materialized.as_document()
            ),
        }

    @classmethod
    def from_document(cls, value: Mapping[str, Any]) -> JournalHead:
        document = _require_exact_document(
            value,
            required={'journal_head_schema_version', 'durable', 'materialized'},
            label='journal head',
        )
        if document['journal_head_schema_version'] != JOURNAL_HEAD_SCHEMA_VERSION:
            raise AlarmPersistenceCorruptionError('journal head schema version is unsupported')
        durable = _optional_position(document['durable'], 'durable')
        materialized = _optional_position(document['materialized'], 'materialized')
        try:
            return cls(durable=durable, materialized=materialized)
        except (TypeError, ValueError) as error:
            raise AlarmPersistenceCorruptionError('journal head is invalid') from error


@dataclass(frozen=True, slots=True)
class EngineCommitMetadata:
    commit_id: str
    cycle_id: str
    priority_group: str
    previous_commit_id: str | None
    evaluated_at: str
    committed_at: str
    alarm_configuration_revision: str
    tool_registry_revision: str
    runtime_artifact_version: str
    affected_alarms: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            'commit_id',
            'cycle_id',
            'priority_group',
            'alarm_configuration_revision',
            'tool_registry_revision',
            'runtime_artifact_version',
        ):
            _require_non_empty_string(getattr(self, name), name)
        _require_priority_group(self.priority_group)
        if self.previous_commit_id is not None:
            _require_non_empty_string(self.previous_commit_id, 'previous_commit_id')
            if self.previous_commit_id == self.commit_id:
                raise ValueError('previous_commit_id must differ from commit_id')
        _require_utc_timestamp(self.evaluated_at, 'evaluated_at')
        _require_utc_timestamp(self.committed_at, 'committed_at')
        if not isinstance(self.affected_alarms, tuple):
            raise TypeError('affected_alarms must be a tuple')
        if not self.affected_alarms:
            raise ValueError('affected_alarms must not be empty')
        seen: set[str] = set()
        for alarm_key in self.affected_alarms:
            _require_non_empty_string(alarm_key, 'affected alarm')
            if alarm_key in seen:
                raise ValueError('affected_alarms must not contain duplicates')
            seen.add(alarm_key)

    def as_document(self) -> JsonDocument:
        return {
            'commit_id': self.commit_id,
            'cycle_id': self.cycle_id,
            'priority_group': self.priority_group,
            'previous_commit_id': self.previous_commit_id,
            'evaluated_at': self.evaluated_at,
            'committed_at': self.committed_at,
            'alarm_configuration_revision': self.alarm_configuration_revision,
            'tool_registry_revision': self.tool_registry_revision,
            'runtime_artifact_version': self.runtime_artifact_version,
            'affected_alarms': list(self.affected_alarms),
        }

    @classmethod
    def from_document(cls, value: Mapping[str, Any]) -> EngineCommitMetadata:
        document = _require_exact_document(
            value,
            required={
                'commit_id',
                'cycle_id',
                'priority_group',
                'previous_commit_id',
                'evaluated_at',
                'committed_at',
                'alarm_configuration_revision',
                'tool_registry_revision',
                'runtime_artifact_version',
                'affected_alarms',
            },
            label='engine commit metadata',
        )
        affected = document['affected_alarms']
        if not isinstance(affected, list):
            raise AlarmPersistenceCorruptionError('affected_alarms must be an array')
        try:
            return cls(
                commit_id=document['commit_id'],
                cycle_id=document['cycle_id'],
                priority_group=document['priority_group'],
                previous_commit_id=document['previous_commit_id'],
                evaluated_at=document['evaluated_at'],
                committed_at=document['committed_at'],
                alarm_configuration_revision=document['alarm_configuration_revision'],
                tool_registry_revision=document['tool_registry_revision'],
                runtime_artifact_version=document['runtime_artifact_version'],
                affected_alarms=tuple(affected),
            )
        except (TypeError, ValueError) as error:
            raise AlarmPersistenceCorruptionError('engine commit metadata is invalid') from error


@dataclass(frozen=True, slots=True)
class GroupRuntimeSnapshot:
    _document: JsonDocument

    def __post_init__(self) -> None:
        try:
            normalized = normalize_json_document(self._document)
        except (TypeError, ValueError) as error:
            raise AlarmPersistenceValidationError('group runtime snapshot is invalid') from error
        _validate_snapshot_document(normalized, corruption=False)
        object.__setattr__(self, '_document', normalized)

    @property
    def priority_group(self) -> str:
        return self._document['priority_group']

    @property
    def last_commit_id(self) -> str:
        return self._document['last_commit_id']

    def as_document(self) -> JsonDocument:
        return copy.deepcopy(self._document)

    @classmethod
    def from_document(cls, value: Mapping[str, Any]) -> GroupRuntimeSnapshot:
        try:
            normalized = normalize_json_document(value)
        except (TypeError, ValueError) as error:
            raise AlarmPersistenceCorruptionError('group runtime snapshot is invalid') from error
        _validate_snapshot_document(normalized, corruption=True)
        return cls(normalized)


@dataclass(frozen=True, slots=True)
class EngineCommitRecord:
    commit: EngineCommitMetadata
    snapshot_after: GroupRuntimeSnapshot
    records: JsonDocument
    record_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.commit, EngineCommitMetadata):
            raise TypeError('commit must be EngineCommitMetadata')
        if not isinstance(self.snapshot_after, GroupRuntimeSnapshot):
            raise TypeError('snapshot_after must be GroupRuntimeSnapshot')
        if self.snapshot_after.priority_group != self.commit.priority_group:
            raise ValueError('snapshot_after priority_group must match commit priority_group')
        if self.snapshot_after.last_commit_id != self.commit.commit_id:
            raise ValueError('snapshot_after last_commit_id must match commit_id')
        normalized_records = _normalize_records(self.records, corruption=False)
        object.__setattr__(self, 'records', normalized_records)
        _require_record_hash(self.record_hash)

    @classmethod
    def create(
        cls,
        *,
        commit: EngineCommitMetadata,
        snapshot_after: GroupRuntimeSnapshot,
        records: Mapping[str, Any] | None = None,
    ) -> EngineCommitRecord:
        normalized_records = _normalize_records(records or {}, corruption=False)
        unsigned = {
            'record_schema_version': ENGINE_COMMIT_RECORD_SCHEMA_VERSION,
            'commit': commit.as_document(),
            'snapshot_after': snapshot_after.as_document(),
            'records': normalized_records,
        }
        from ada.alarms.persistence.serialization import build_record_hash

        return cls(
            commit=commit,
            snapshot_after=snapshot_after,
            records=normalized_records,
            record_hash=build_record_hash(unsigned),
        )

    def unsigned_document(self) -> JsonDocument:
        return {
            'record_schema_version': ENGINE_COMMIT_RECORD_SCHEMA_VERSION,
            'commit': self.commit.as_document(),
            'snapshot_after': self.snapshot_after.as_document(),
            'records': copy.deepcopy(self.records),
        }

    def as_document(self) -> JsonDocument:
        return {**self.unsigned_document(), 'record_hash': self.record_hash}

    @classmethod
    def from_document(cls, value: Mapping[str, Any]) -> EngineCommitRecord:
        document = _require_exact_document(
            value,
            required={
                'record_schema_version',
                'commit',
                'snapshot_after',
                'records',
                'record_hash',
            },
            label='engine commit record',
        )
        if document['record_schema_version'] != ENGINE_COMMIT_RECORD_SCHEMA_VERSION:
            raise AlarmPersistenceCorruptionError(
                'engine commit record schema version is unsupported'
            )
        if not isinstance(document['commit'], Mapping):
            raise AlarmPersistenceCorruptionError('engine commit record commit must be an object')
        if not isinstance(document['snapshot_after'], Mapping):
            raise AlarmPersistenceCorruptionError(
                'engine commit record snapshot_after must be an object'
            )
        if not isinstance(document['records'], Mapping):
            raise AlarmPersistenceCorruptionError('engine commit record records must be an object')
        commit = EngineCommitMetadata.from_document(document['commit'])
        snapshot = GroupRuntimeSnapshot.from_document(document['snapshot_after'])
        records = _normalize_records(document['records'], corruption=True)
        record_hash = document['record_hash']
        if not isinstance(record_hash, str):
            raise AlarmPersistenceCorruptionError('engine commit record hash is invalid')
        try:
            record = cls(
                commit=commit,
                snapshot_after=snapshot,
                records=records,
                record_hash=record_hash,
            )
        except (TypeError, ValueError) as error:
            raise AlarmPersistenceCorruptionError('engine commit record is invalid') from error
        from ada.alarms.persistence.serialization import build_record_hash

        if build_record_hash(record.unsigned_document()) != record.record_hash:
            raise AlarmPersistenceCorruptionError(
                'engine commit record hash does not match payload'
            )
        return record


@dataclass(frozen=True, slots=True)
class JournalEntry:
    record: EngineCommitRecord
    start_offset: int
    end: JournalPosition

    def __post_init__(self) -> None:
        if not isinstance(self.record, EngineCommitRecord):
            raise TypeError('record must be an EngineCommitRecord')
        if isinstance(self.start_offset, bool) or not isinstance(self.start_offset, int):
            raise TypeError('start_offset must be an int')
        if self.start_offset < 0:
            raise ValueError('start_offset must be greater than or equal to zero')
        if not isinstance(self.end, JournalPosition):
            raise TypeError('end must be a JournalPosition')
        if self.start_offset >= self.end.byte_offset:
            raise ValueError('start_offset must be lower than end byte_offset')
        if self.end.commit_id != self.record.commit.commit_id:
            raise ValueError('journal entry end commit_id must match record commit_id')


@dataclass(frozen=True, slots=True)
class CommitBatchResult:
    record_count: int
    bytes_appended: int
    durable: JournalPosition
    materialized: JournalPosition
    sealed_segment_count: int


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    durable: JournalPosition | None
    materialized: JournalPosition | None
    applied_count: int
    skipped_count: int
    discarded_tail_bytes: int
    sealed_segment_count: int


def segment_id_for_evaluated_at(value: str, *, part: int = 0) -> str:
    timestamp = _require_utc_timestamp(value, 'evaluated_at')
    if isinstance(part, bool) or not isinstance(part, int):
        raise TypeError('part must be an int')
    if part < 0 or part > 9999:
        raise ValueError('part must be between 0 and 9999')
    return f'{timestamp:%Y-%m-%dT%HZ}#{part:04d}'


def parse_segment_id(value: str) -> tuple[int, int, int, int, int]:
    _require_segment_id(value)
    date_hour, part_text = value.split('#', maxsplit=1)
    timestamp = datetime.strptime(date_hour, '%Y-%m-%dT%HZ').replace(tzinfo=UTC)
    return timestamp.year, timestamp.month, timestamp.day, timestamp.hour, int(part_text)


def _validate_position_order(earlier: JournalPosition, later: JournalPosition) -> None:
    earlier_key = (earlier.segment_id, earlier.byte_offset)
    later_key = (later.segment_id, later.byte_offset)
    if earlier_key > later_key:
        raise ValueError('materialized position must not be ahead of durable position')
    if earlier_key == later_key and earlier.commit_id != later.commit_id:
        raise ValueError('equal journal positions must reference the same commit_id')


def _optional_position(value: Any, name: str) -> JournalPosition | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise AlarmPersistenceCorruptionError(f'{name} journal position must be an object or null')
    return JournalPosition.from_document(value)


def _normalize_records(value: Mapping[str, Any], *, corruption: bool) -> JsonDocument:
    error_type = AlarmPersistenceCorruptionError if corruption else AlarmPersistenceValidationError
    try:
        document = normalize_json_document(value)
    except (TypeError, ValueError) as error:
        raise error_type('engine commit records payload is invalid') from error
    unknown = set(document) - _RECORD_COLLECTIONS
    if unknown:
        raise error_type('engine commit records contain unsupported collections')
    normalized: JsonDocument = {}
    for name, items in document.items():
        if not isinstance(items, list):
            raise error_type(f'{name} must be an array')
        for item in items:
            if not isinstance(item, dict):
                raise error_type(f'{name} must contain only JSON objects')
        if items:
            normalized[name] = items
    return normalized


def _validate_snapshot_document(document: JsonDocument, *, corruption: bool) -> None:
    error_type = AlarmPersistenceCorruptionError if corruption else AlarmPersistenceValidationError
    required = {'snapshot_schema_version', 'priority_group', 'last_commit_id', 'alarms'}
    optional = {'state_basis', 'episode'}
    if set(document) - required - optional or required - set(document):
        raise error_type('group runtime snapshot has unexpected or missing fields')
    if document['snapshot_schema_version'] != GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION:
        raise error_type('group runtime snapshot schema version is unsupported')
    try:
        _require_priority_group(document['priority_group'])
        _require_non_empty_string(document['last_commit_id'], 'last_commit_id')
        _validate_state_basis(document.get('state_basis'), error_type)
        _validate_episode(document.get('episode'), error_type)
        _validate_alarm_states(document['alarms'], error_type)
    except (TypeError, ValueError) as error:
        if isinstance(error, error_type):
            raise
        raise error_type('group runtime snapshot is invalid') from error


def _validate_state_basis(
    value: Any,
    error_type: type[AlarmPersistenceValidationError] | type[AlarmPersistenceCorruptionError],
) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {
        'alarm_configuration_revision',
        'tool_registry_revision',
    }:
        raise error_type('state_basis is invalid')
    _require_non_empty_string(value['alarm_configuration_revision'], 'alarm_configuration_revision')
    _require_non_empty_string(value['tool_registry_revision'], 'tool_registry_revision')


def _validate_episode(
    value: Any,
    error_type: type[AlarmPersistenceValidationError] | type[AlarmPersistenceCorruptionError],
) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {'episode_id', 'started_at'}:
        raise error_type('episode is invalid')
    _require_non_empty_string(value['episode_id'], 'episode_id')
    _require_utc_timestamp(value['started_at'], 'episode.started_at')


def _validate_alarm_states(
    value: Any,
    error_type: type[AlarmPersistenceValidationError] | type[AlarmPersistenceCorruptionError],
) -> None:
    if not isinstance(value, dict):
        raise error_type('alarms must be an object')
    for alarm_key, state in value.items():
        _require_non_empty_string(alarm_key, 'alarm_key')
        if not isinstance(state, dict):
            raise error_type('alarm runtime state must be an object')
        required = {'last_commit_id'}
        optional = {'occurrence', 'management_effect', 'deactivation_effect'}
        if set(state) - required - optional or required - set(state):
            raise error_type('alarm runtime state has unexpected or missing fields')
        _require_non_empty_string(state['last_commit_id'], 'alarm last_commit_id')
        _validate_occurrence(state.get('occurrence'), error_type)
        _validate_management_effect(state.get('management_effect'), error_type)
        _validate_deactivation_effect(state.get('deactivation_effect'), error_type)


def _validate_occurrence(
    value: Any,
    error_type: type[AlarmPersistenceValidationError] | type[AlarmPersistenceCorruptionError],
) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise error_type('occurrence must be an object')
    required = {
        'occurrence_id',
        'started_at',
        'configuration_revision_at_start',
        'tool_registry_revision_at_start',
        'last_evaluation',
        'management_cycle',
        'assignments',
        'pending_assignments',
    }
    optional = {'technical_hold', 'next_evidence_due_at'}
    if set(value) - required - optional or required - set(value):
        raise error_type('occurrence has unexpected or missing fields')
    for name in (
        'occurrence_id',
        'configuration_revision_at_start',
        'tool_registry_revision_at_start',
    ):
        _require_non_empty_string(value[name], name)
    _require_utc_timestamp(value['started_at'], 'occurrence.started_at')
    cycle = value['management_cycle']
    if isinstance(cycle, bool) or not isinstance(cycle, int) or cycle <= 0:
        raise error_type('management_cycle must be a positive int')
    _validate_last_evaluation(value['last_evaluation'], error_type)
    _validate_technical_hold(value.get('technical_hold'), error_type)
    _validate_timestamp_map(value['assignments'], 'assigned_at', error_type)
    _validate_timestamp_map(value['pending_assignments'], 'due_at', error_type)
    due_at = value.get('next_evidence_due_at')
    if due_at is not None:
        _require_utc_timestamp(due_at, 'next_evidence_due_at')


def _validate_last_evaluation(
    value: Any,
    error_type: type[AlarmPersistenceValidationError] | type[AlarmPersistenceCorruptionError],
) -> None:
    if not isinstance(value, dict):
        raise error_type('last_evaluation must be an object')
    required = {'status', 'evaluated_at'}
    optional = {'error_key'}
    if set(value) - required - optional or required - set(value):
        raise error_type('last_evaluation has unexpected or missing fields')
    if value['status'] not in {'ACTIVE', 'INACTIVE', 'ERROR'}:
        raise error_type('last_evaluation status is invalid')
    _require_utc_timestamp(value['evaluated_at'], 'last_evaluation.evaluated_at')
    if value.get('error_key') is not None:
        _require_non_empty_string(value['error_key'], 'last_evaluation.error_key')


def _validate_technical_hold(
    value: Any,
    error_type: type[AlarmPersistenceValidationError] | type[AlarmPersistenceCorruptionError],
) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {'started_at', 'due_at'}:
        raise error_type('technical_hold is invalid')
    _require_utc_timestamp(value['started_at'], 'technical_hold.started_at')
    _require_utc_timestamp(value['due_at'], 'technical_hold.due_at')


def _validate_timestamp_map(
    value: Any,
    timestamp_key: str,
    error_type: type[AlarmPersistenceValidationError] | type[AlarmPersistenceCorruptionError],
) -> None:
    if not isinstance(value, dict):
        raise error_type(f'{timestamp_key} map must be an object')
    for tool_key, payload in value.items():
        _require_non_empty_string(tool_key, 'tool_key')
        if not isinstance(payload, dict) or set(payload) != {timestamp_key}:
            raise error_type(f'{timestamp_key} entry is invalid')
        _require_utc_timestamp(payload[timestamp_key], timestamp_key)


def _validate_management_effect(
    value: Any,
    error_type: type[AlarmPersistenceValidationError] | type[AlarmPersistenceCorruptionError],
) -> None:
    if value is None:
        return
    required = {'effect_id', 'source_occurrence_id', 'effective_at', 'reappearance_due_at'}
    if not isinstance(value, dict) or set(value) != required:
        raise error_type('management_effect is invalid')
    _require_non_empty_string(value['effect_id'], 'management effect_id')
    _require_non_empty_string(value['source_occurrence_id'], 'source_occurrence_id')
    _require_utc_timestamp(value['effective_at'], 'management_effect.effective_at')
    _require_utc_timestamp(value['reappearance_due_at'], 'management_effect.reappearance_due_at')


def _validate_deactivation_effect(
    value: Any,
    error_type: type[AlarmPersistenceValidationError] | type[AlarmPersistenceCorruptionError],
) -> None:
    if value is None:
        return
    required = {'effect_id', 'effective_from', 'effective_until'}
    if not isinstance(value, dict) or set(value) != required:
        raise error_type('deactivation_effect is invalid')
    _require_non_empty_string(value['effect_id'], 'deactivation effect_id')
    _require_utc_timestamp(value['effective_from'], 'deactivation_effect.effective_from')
    _require_utc_timestamp(value['effective_until'], 'deactivation_effect.effective_until')


def _require_exact_document(
    value: Mapping[str, Any],
    *,
    required: set[str],
    label: str,
) -> JsonDocument:
    if not isinstance(value, Mapping):
        raise AlarmPersistenceCorruptionError(f'{label} must be an object')
    try:
        document = normalize_json_document(value)
    except (TypeError, ValueError) as error:
        raise AlarmPersistenceCorruptionError(f'{label} is invalid') from error
    if set(document) != required:
        raise AlarmPersistenceCorruptionError(f'{label} has unexpected or missing fields')
    return document


def _require_record_hash(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
        raise ValueError('record_hash must use sha256:<64 lowercase hex characters>')


def _require_segment_id(value: str) -> None:
    if not isinstance(value, str) or _SEGMENT_ID_PATTERN.fullmatch(value) is None:
        raise ValueError('segment_id must use YYYY-MM-DDTHHZ#NNNN')
    try:
        date_hour, _ = value.split('#', maxsplit=1)
        datetime.strptime(date_hour, '%Y-%m-%dT%HZ')
    except ValueError as error:
        raise ValueError('segment_id contains an invalid UTC hour') from error


def _require_priority_group(value: str) -> None:
    _require_non_empty_string(value, 'priority_group')
    if value in {'.', '..'} or '/' in value or '\\' in value or '\x00' in value:
        raise ValueError('priority_group must be a safe path segment')


def _require_non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f'{name} must be a string')
    normalized = value.strip()
    if not normalized:
        raise ValueError(f'{name} must not be empty')
    return normalized


def _require_utc_timestamp(value: Any, name: str) -> datetime:
    _require_non_empty_string(value, name)
    text = value[:-1] + '+00:00' if value.endswith('Z') else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f'{name} must be an ISO-8601 timestamp') from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f'{name} must be timezone-aware')
    if parsed.utcoffset() != timedelta(0):
        raise ValueError(f'{name} must use UTC timezone')
    return parsed.astimezone(UTC)
