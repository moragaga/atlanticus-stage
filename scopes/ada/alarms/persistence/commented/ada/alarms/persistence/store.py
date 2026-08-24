# Espejo pedagógico de la orquestación durable/materialized y recovery.
# AlarmPersistence compone AtomicJsonStore para documentos reemplazables y mantiene el WAL como responsabilidad del dominio.
# La confirmación sigue el orden WAL fsync -> JournalHead.durable -> snapshots -> JournalHead.materialized.
# Las mutaciones físicas irreversibles se ejecutan dentro del MutationFence inyectado por el consumidor.
# Recovery reproduce snapshot_after de commits durable no materializados y nunca vuelve a evaluar la decisión del Engine.
# La autoridad lógica y el fencing físico se inyectan para evitar acoplar este paquete directamente a Job Runtime.

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path

from ada.alarms.persistence.errors import (
    AlarmPersistenceConflictError,
    AlarmPersistenceCorruptionError,
    AlarmPersistenceWriteError,
    AlarmRecoveryRequiredError,
)
from ada.alarms.persistence.journal import EngineJournal
from ada.alarms.persistence.models import (
    CommitBatchResult,
    EngineCommitRecord,
    GroupRuntimeSnapshot,
    JournalEntry,
    JournalHead,
    JournalPosition,
    RecoveryResult,
    segment_id_for_evaluated_at,
)
from ada.alarms.persistence.paths import AlarmPersistencePaths
from atlanticus.state import AtomicJsonStore, StateError

AuthorityCheck = Callable[[], None]
MutationFence = Callable[[], AbstractContextManager[None]]


class AlarmPersistence:
    def __init__(
        self,
        *,
        shared_volume_path: str | Path,
        max_state_document_bytes: int | None = None,
    ) -> None:
        self._paths = AlarmPersistencePaths(shared_volume_path=shared_volume_path)
        self._state = AtomicJsonStore(
            root_path=self._paths.alarms_root,
            max_document_bytes=max_state_document_bytes,
        )
        self._journal = EngineJournal(paths=self._paths)
        self._write_lock = threading.RLock()

    @property
    def paths(self) -> AlarmPersistencePaths:
        return self._paths

    def read_head(self) -> JournalHead:
        try:
            document = self._state.read(self._paths.journal_head_relative)
        except StateError as error:
            raise AlarmPersistenceCorruptionError(
                'could not read Alarm Engine journal head'
            ) from error
        if document is None:
            return JournalHead()
        return JournalHead.from_document(document)

    def read_snapshot(self, priority_group: str) -> GroupRuntimeSnapshot | None:
        try:
            document = self._state.read(self._paths.group_snapshot_relative(priority_group))
        except StateError as error:
            raise AlarmPersistenceCorruptionError(
                f'could not read Alarm Engine snapshot for {priority_group}'
            ) from error
        if document is None:
            return None
        return GroupRuntimeSnapshot.from_document(document)

    def list_snapshots(self) -> tuple[GroupRuntimeSnapshot, ...]:
        root = self._paths.alarms_root / 'runtime' / 'state' / 'groups'
        if not root.exists():
            return ()
        snapshots: list[GroupRuntimeSnapshot] = []
        for path in sorted(root.glob('*.json')):
            snapshots.append(self._read_snapshot_path(path))
        return tuple(snapshots)

    def read_durable_records(
        self,
        *,
        after: JournalPosition | None = None,
    ) -> tuple[JournalEntry, ...]:
        head = self.read_head()
        if head.durable is None or after == head.durable:
            return ()
        if after is not None and _position_key(after) > _position_key(head.durable):
            raise ValueError('after must not be ahead of durable journal head')
        return self._journal.read_entries(after=after, through=head.durable)

    def commit_batch(
        self,
        records: Sequence[EngineCommitRecord],
        *,
        assert_authority: AuthorityCheck,
        fenced_mutation: MutationFence,
    ) -> CommitBatchResult:
        authority = _require_authority(assert_authority)
        mutation = _require_mutation_fence(fenced_mutation)
        ordered = _ordered_records(records)
        with self._write_lock:
            authority()
            head = self.read_head()
            if not head.aligned:
                raise AlarmRecoveryRequiredError(
                    'Alarm Engine journal must be recovered before committing new work'
                )
            self._validate_previous_state(ordered)
            segment_id = segment_id_for_evaluated_at(ordered[0].commit.evaluated_at)
            with mutation():
                _require_unchanged_head(self.read_head(), head, stage='WAL append')
                self._journal.discard_unconfirmed_tail(head.durable)
                sealed_count = 0
                if head.durable is not None and segment_id > head.durable.segment_id:
                    sealed_count = self._journal.seal_before(segment_id)
                self._journal.verify_append_position(durable=head.durable, segment_id=segment_id)
                entries = self._journal.append_batch(ordered)
            final_position = entries[-1].end
            durable_head = JournalHead(durable=final_position, materialized=head.materialized)
            with mutation():
                _require_unchanged_head(self.read_head(), head, stage='durable publication')
                self._replace_head(durable_head)
            for entry in entries:
                with mutation():
                    _require_durable_head(
                        self.read_head(), durable_head, stage='snapshot materialization'
                    )
                    self._materialize_entry(entry)
            completed_head = JournalHead(
                durable=final_position,
                materialized=final_position,
            )
            with mutation():
                _require_durable_head(
                    self.read_head(), durable_head, stage='materialized publication'
                )
                self._replace_head(completed_head)
            return CommitBatchResult(
                record_count=len(entries),
                bytes_appended=sum(entry.end.byte_offset - entry.start_offset for entry in entries),
                durable=final_position,
                materialized=final_position,
                sealed_segment_count=sealed_count,
            )

    def recover(
        self,
        *,
        assert_authority: AuthorityCheck,
        fenced_mutation: MutationFence,
    ) -> RecoveryResult:
        authority = _require_authority(assert_authority)
        mutation = _require_mutation_fence(fenced_mutation)
        with self._write_lock:
            authority()
            head = self.read_head()
            if head.durable is None and self._has_group_snapshots():
                raise AlarmPersistenceCorruptionError(
                    'Alarm Engine snapshots exist without a durable journal head'
                )
            with mutation():
                _require_unchanged_head(self.read_head(), head, stage='recovery tail discard')
                discarded = self._journal.discard_unconfirmed_tail(head.durable)
            authority()
            self._journal.validate_durable_region(head.durable)
            if head.durable is None:
                return RecoveryResult(
                    durable=None,
                    materialized=None,
                    applied_count=0,
                    skipped_count=0,
                    discarded_tail_bytes=discarded,
                    sealed_segment_count=0,
                )
            if head.materialized == head.durable:
                with mutation():
                    _require_unchanged_head(self.read_head(), head, stage='recovery sealing')
                    sealed_count = self._journal.seal_before(head.durable.segment_id)
                return RecoveryResult(
                    durable=head.durable,
                    materialized=head.materialized,
                    applied_count=0,
                    skipped_count=0,
                    discarded_tail_bytes=discarded,
                    sealed_segment_count=sealed_count,
                )
            entries = self._journal.read_entries(after=head.materialized, through=head.durable)
            applied = 0
            skipped = 0
            current_materialized = head.materialized
            current_head = head
            for entry in entries:
                with mutation():
                    _require_unchanged_head(
                        self.read_head(),
                        current_head,
                        stage='recovery snapshot materialization',
                    )
                    was_applied = self._materialize_entry(entry)
                next_head = JournalHead(durable=head.durable, materialized=entry.end)
                with mutation():
                    _require_unchanged_head(
                        self.read_head(),
                        current_head,
                        stage='recovery materialized publication',
                    )
                    self._replace_head(next_head)
                if was_applied:
                    applied += 1
                else:
                    skipped += 1
                current_materialized = entry.end
                current_head = next_head
            with mutation():
                _require_unchanged_head(self.read_head(), current_head, stage='recovery sealing')
                sealed_count = self._journal.seal_before(head.durable.segment_id)
            return RecoveryResult(
                durable=head.durable,
                materialized=current_materialized,
                applied_count=applied,
                skipped_count=skipped,
                discarded_tail_bytes=discarded,
                sealed_segment_count=sealed_count,
            )

    def _validate_previous_state(self, records: Sequence[EngineCommitRecord]) -> None:
        for record in records:
            current = self.read_snapshot(record.commit.priority_group)
            if current is None:
                if record.commit.previous_commit_id is not None:
                    raise AlarmPersistenceConflictError(
                        'engine commit previous_commit_id does not match current group head'
                    )
                continue
            if current.last_commit_id == record.commit.commit_id:
                raise AlarmPersistenceConflictError('engine commit is already materialized')
            if current.last_commit_id != record.commit.previous_commit_id:
                raise AlarmPersistenceConflictError(
                    'engine commit previous_commit_id does not match current group head'
                )

    def _materialize_entry(self, entry: JournalEntry) -> bool:
        record = entry.record
        current = self.read_snapshot(record.commit.priority_group)
        if current is not None and current.last_commit_id == record.commit.commit_id:
            return False
        expected = record.commit.previous_commit_id
        if current is None:
            if expected is not None:
                raise AlarmPersistenceCorruptionError(
                    'group snapshot is missing before a non-initial durable commit'
                )
        elif current.last_commit_id != expected:
            raise AlarmPersistenceCorruptionError(
                'group snapshot head does not match durable commit chain'
            )
        try:
            self._state.replace(
                self._paths.group_snapshot_relative(record.commit.priority_group),
                record.snapshot_after.as_document(),
            )
        except StateError as error:
            raise AlarmPersistenceWriteError(
                'could not materialize Alarm Engine snapshot'
            ) from error
        return True

    def _replace_head(self, head: JournalHead) -> None:
        try:
            self._state.replace(self._paths.journal_head_relative, head.as_document())
        except StateError as error:
            raise AlarmPersistenceWriteError(
                'could not publish Alarm Engine journal head'
            ) from error

    def _read_snapshot_path(self, path: Path) -> GroupRuntimeSnapshot:
        try:
            relative = path.relative_to(self._paths.alarms_root)
            document = self._state.read(relative)
        except (StateError, ValueError) as error:
            raise AlarmPersistenceCorruptionError('could not read Alarm Engine snapshot') from error
        if document is None:
            raise AlarmPersistenceCorruptionError('Alarm Engine snapshot disappeared during read')
        return GroupRuntimeSnapshot.from_document(document)

    def _has_group_snapshots(self) -> bool:
        root = self._paths.alarms_root / 'runtime' / 'state' / 'groups'
        return root.exists() and any(root.glob('*.json'))


def _require_authority(value: AuthorityCheck) -> AuthorityCheck:
    if not callable(value):
        raise TypeError('assert_authority must be callable')
    return value


def _require_mutation_fence(value: MutationFence) -> MutationFence:
    if not callable(value):
        raise TypeError('fenced_mutation must be callable')
    return value


def _require_unchanged_head(current: JournalHead, expected: JournalHead, *, stage: str) -> None:
    if current != expected:
        raise AlarmPersistenceConflictError(f'Alarm Engine journal head changed before {stage}')


def _require_durable_head(current: JournalHead, expected: JournalHead, *, stage: str) -> None:
    if current.durable != expected.durable or current.materialized != expected.materialized:
        raise AlarmPersistenceConflictError(f'Alarm Engine journal head changed before {stage}')


def _ordered_records(records: Sequence[EngineCommitRecord]) -> tuple[EngineCommitRecord, ...]:
    if isinstance(records, str | bytes) or not isinstance(records, Sequence):
        raise TypeError('records must be a sequence')
    if not records:
        raise ValueError('records must not be empty')
    normalized: list[EngineCommitRecord] = []
    cycle_id: str | None = None
    groups: set[str] = set()
    segment_id: str | None = None
    for record in records:
        if not isinstance(record, EngineCommitRecord):
            raise TypeError('records must contain only EngineCommitRecord values')
        if cycle_id is None:
            cycle_id = record.commit.cycle_id
        elif record.commit.cycle_id != cycle_id:
            raise ValueError('all records in a batch must share cycle_id')
        if record.commit.priority_group in groups:
            raise ValueError('a batch must contain at most one commit per priority_group')
        groups.add(record.commit.priority_group)
        current_segment = segment_id_for_evaluated_at(record.commit.evaluated_at)
        if segment_id is None:
            segment_id = current_segment
        elif current_segment != segment_id:
            raise ValueError('all records in a batch must belong to the same UTC hour')
        normalized.append(record)
    return tuple(
        sorted(normalized, key=lambda item: (item.commit.priority_group, item.commit.commit_id))
    )


def _position_key(value: JournalPosition) -> tuple[str, int]:
    return value.segment_id, value.byte_offset
